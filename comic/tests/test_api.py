import httpx
import pytest
from fastapi.testclient import TestClient

from app import api, comics
from app.api import app
from app.comics import Balloon, Limits, Moment, ScriptPage, ScriptPanel


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue_job(self, name, *args, _job_id=None):
        self.jobs.append((name, *args))
        return object()


@pytest.fixture
def client(sessions_db):
    app.state.queue = FakeQueue()
    with TestClient(app) as c:
        yield c
    app.state.queue = None


def jobs(client):
    return client.app.state.queue.jobs


def scripted(status="script") -> comics.Comic:
    c = comics.create("ended1", "Session 12")
    c.status = status
    c.events = "1. Örkit hyökkäävät"
    c.moments = [Moment(title="Kuulustelu <b>", summary="s"), Moment(title="Tikari")]
    c.script = [
        ScriptPage(
            title="Ei-kuolettava kuulustelu",
            characters=["Pentik", "Rintaro"],
            panels=[
                ScriptPanel(
                    visual="Pentik points", balloons=[Balloon(speaker="Pentik", text="Ota!")]
                ),
                ScriptPanel(visual="Rintaro strikes"),
            ],
        )
    ]
    comics.save(c)
    return c


def test_dashboard_has_the_cards(client):
    html = client.get("/dashboard").text
    for fragment in ("/api/v1/comics", "/api/v1/bible", "/api/v1/limits"):
        assert fragment in html
    assert "/api/v1/dashboard/mode" not in html  # no comic mode any more


def test_comics_card_offers_finished_transcripts(client):
    html = client.get("/api/v1/comics").text
    assert "<option value='ended1'>" in html and "live1" not in html
    assert "Session &lt;b&gt;12&lt;/b&gt;" in html and "Start comic" in html


def test_start_comic_creates_and_queues_extraction(client):
    html = client.post("/api/v1/comics", data={"session_id": "ended1"}).text
    [c] = comics.list_comics()
    assert c.session_id == "ended1" and c.status == "extracting"
    assert jobs(client) == [("extract_job", c.id)]
    assert "Working" in html


def test_start_refuses_unknown_or_live_sessions(client):
    assert "Unknown session" in client.post("/api/v1/comics", data={"session_id": "x"}).text
    assert "still recording" in client.post("/api/v1/comics", data={"session_id": "live1"}).text
    assert comics.list_comics() == [] and jobs(client) == []


def test_events_view_lets_you_choose_moments(client):
    c = scripted(status="events")
    c.script = []  # events extracted, no script yet
    comics.save(c)
    html = client.get(f"/api/v1/comics/{c.id}").text
    assert "Kuulustelu &lt;b&gt;" in html and 'name="chosen"' in html
    client.post(f"/api/v1/comics/{c.id}/script", data={"chosen": ["1"], "own": "Käl heittää"})
    assert jobs(client) == [("script_job", c.id, [1], "Käl heittää")]
    assert comics.load(c.id).status == "scripting"


def test_script_editor_round_trip(client):
    c = scripted()
    html = client.get(f"/api/v1/comics/{c.id}").text
    assert 'name="page-0-title"' in html and "Pentik: Ota!" in html
    form = {
        "page-0-title": "Uusi otsikko",
        "page-0-characters": "Pentik, Rintaro",
        "page-0-panel-0-visual": "Pentik points his flail",
        "page-0-panel-0-balloons": "Pentik: Ota tuo elävänä!\nRintaro: Hups.",
        "page-0-panel-1-visual": "Rintaro strikes",
        "page-0-panel-1-balloons": "",
    }
    client.post(f"/api/v1/comics/{c.id}/script/save", data=form)
    page = comics.load(c.id).script[0]
    assert page.title == "Uusi otsikko" and page.characters == ["Pentik", "Rintaro"]
    assert [(b.speaker, b.text) for b in page.panels[0].balloons] == [
        ("Pentik", "Ota tuo elävänä!"),
        ("Rintaro", "Hups."),
    ]
    assert page.panels[1].balloons == []


def test_draw_all_and_redraw_one_page(client):
    c = scripted()
    client.post(f"/api/v1/comics/{c.id}/draw", data={})
    assert comics.load(c.id).status == "drawing"
    c = comics.load(c.id)
    c.status = "done"  # the worker finished
    comics.save(c)
    # The page's own instruction field, as the Redraw button sends it from the editor.
    client.post(f"/api/v1/comics/{c.id}/draw", data={"page": "0", "extra-0": "Kilpi näkyviin"})
    assert jobs(client) == [("draw_job", c.id, None, ""), ("draw_job", c.id, 0, "Kilpi näkyviin")]


def test_drawing_saves_unsaved_script_edits_first(client):
    c = scripted()
    client.post(f"/api/v1/comics/{c.id}/draw", data={"page-0-title": "Muokattu"})
    assert comics.load(c.id).script[0].title == "Muokattu"


def test_a_busy_comic_refuses_new_steps(client):
    c = scripted(status="drawing")
    html = client.post(f"/api/v1/comics/{c.id}/draw", data={}).text
    assert "already working" in html and jobs(client) == []
    assert 'hx-trigger="every 3s"' in client.get(f"/api/v1/comics/{c.id}").text


def test_pages_tokens_and_messages_are_shown(client):
    c = scripted(status="budget")
    c.message = "Budget reached (4000 / 5000 tokens)"
    c.image_tokens = 4000
    c.text_tokens = 34_000
    comics.add_page_version(c, 0, b"\x89PNG fake")
    c.pages[0].check = "missing: Ota!"
    comics.save(c)
    comics.save_limits(Limits(token_budget_per_comic=5000))
    html = client.get(f"/api/v1/comics/{c.id}").text
    assert "Budget reached" in html and "4000 / 5000" in html and "34000" in html
    assert f"/api/v1/comics/{c.id}/pages/page_1_v1.png" in html and "missing: Ota!" in html
    r = client.get(f"/api/v1/comics/{c.id}/pages/page_1_v1.png")
    assert r.status_code == 200 and r.content == b"\x89PNG fake"
    assert client.get(f"/api/v1/comics/{c.id}/pages/comic.json").status_code == 404


def test_unknown_comic_is_404(client):
    assert client.get("/api/v1/comics/nope").status_code == 404


def test_limits_card_saves_the_budget(client):
    html = client.get("/api/v1/limits").text
    assert 'value="80000"' in html and 'name="look_check"' in html and "checked" in html
    html = client.post(
        "/api/v1/limits", data={"token_budget_per_comic": "3000", "max_auto_redraws_per_page": "0"}
    ).text  # an unticked checkbox isn't sent: look check off
    assert comics.load_limits() == Limits(
        token_budget_per_comic=3000, max_auto_redraws_per_page=0, look_check=False
    )
    assert "Saved" in html


def test_limits_refuse_nonsense(client):
    html = client.post(
        "/api/v1/limits", data={"token_budget_per_comic": "-5", "max_auto_redraws_per_page": "9"}
    ).text
    assert "must be" in html and comics.load_limits() == Limits()


def test_transcript_preview_is_escaped_and_short(client):
    html = client.get("/api/v1/dashboard/transcript?session_id=ended1").text
    assert "GM: Örkit hyökkäävät." in html
    assert client.get("/api/v1/dashboard/transcript?session_id=nope").status_code == 404


def test_dashboard_sessions_are_a_read_only_overview(client):
    html = client.get("/api/v1/dashboard/sessions").text
    assert "Session &lt;b&gt;12&lt;/b&gt;" in html and "<b>12</b>" not in html
    assert "hx-post" not in html and "Live" in html


def test_containers_show_only_this_stack(client, monkeypatch):
    def container(name, project, state):
        labels = {"com.docker.compose.project": project} if project else {}
        return {"Names": [f"/{name}"], "State": state, "Labels": labels}

    seen = {}

    def proxy(request: httpx.Request) -> httpx.Response:
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                container("vtt-stt-worker", "vtt-automaton", "running"),
                container("vtt-automaton-redis-1", "vtt-automaton", "exited"),
                container("ifc-checker-app-1", "ifc-checker", "running"),
                container("some-standalone", None, "running"),
            ],
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(proxy), **kw)
    )
    html = client.get("/api/v1/dashboard/containers").text
    assert "vtt-stt-worker" in html and "vtt-automaton-redis-1" in html
    assert "ifc-checker" not in html and "some-standalone" not in html
    # Stopped containers are listed too.
    assert seen["query"] == {"all": "true"}


def test_compare_grid_shows_rounds_side_by_side(client):
    c = scripted(status="done")
    info = comics.VersionInfo
    comics.add_page_version(c, 0, b"a", info(round="r1", refs={"Pentik": "image"}))
    comics.add_page_version(
        c, 0, b"b", info(round="r2", refs={"Pentik": "sheet_1.png"}, anchor=True)
    )
    html = client.get(f"/api/v1/comics/{c.id}").text
    assert "Compare" in html
    assert "Pentik: image" in html and "Pentik: sheet_1.png · style anchor" in html
    grid = html[html.index("Compare drawing rounds") :]
    assert grid.index("page_1_v1.png") < grid.index("page_1_v2.png")  # round 1, then round 2
    assert f"/api/v1/comics/{c.id}/pages/page_1_v2.png/anchor" in html


def test_look_results_are_shown_under_the_page(client):
    c = scripted(status="done")
    comics.add_page_version(c, 0, b"a", comics.VersionInfo(round="r1", previous="page_0.png"))
    c.pages[0].check, c.pages[0].looks = "ok", "Pentik: red castle on a white tabard"
    comics.save(c)
    html = client.get(f"/api/v1/comics/{c.id}").text
    assert "Looks: Pentik: red castle on a white tabard" in html


def test_preview_shows_which_speaker_tags_are_in_the_bible(client):
    from app import bible

    bible.add_character("Valeros")
    html = client.get("/api/v1/dashboard/transcript?session_id=ended1").text
    assert "✓ Valeros" in html
    bible.delete_character("valeros")
    html = client.get("/api/v1/dashboard/transcript?session_id=ended1").text
    assert "✗ Valeros" in html and "not in the bible" in html


def test_limits_card_sets_own_images_per_page(client):
    assert 'name="page_reference_images" value="1"' in client.get("/api/v1/limits").text
    form = {"token_budget_per_comic": "3000", "max_auto_redraws_per_page": "0"}
    client.post("/api/v1/limits", data={**form, "page_reference_images": "2"})
    assert comics.load_limits().page_reference_images == 2
    html = client.post("/api/v1/limits", data={**form, "page_reference_images": "7"}).text
    assert "must be" in html and comics.load_limits().page_reference_images == 2
