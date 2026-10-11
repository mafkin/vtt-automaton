import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import api, bible
from app.api import app
from app.llm import CharacterDraft


@pytest.fixture
def client(sessions_db):
    app.state.queue = object()  # character endpoints never queue jobs
    with TestClient(app) as c:
        yield c
    app.state.queue = None


def png(colour="red", size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, "PNG")
    return buf.getvalue()


def toast(r) -> dict:
    """The notification an action sends with its fragment (HX-Trigger header)."""
    return json.loads(r.headers["HX-Trigger"])["toast"]


def add(client, name="Rintaro") -> str:
    r = client.post("/api/v1/bible/characters", data={"name": name})
    return r.headers["HX-Redirect"].split("?")[0].rsplit("/", 1)[1]


def upload(client, cid, *colours):
    files = [("files", (f"{c}.png", png(c), "image/png")) for c in colours]
    return client.post(f"/api/v1/bible/characters/{cid}/images", files=files)


# --- the Cast page and the Campaign & style page --------------------------------------------


def test_an_empty_cast_offers_to_add_the_first_character(client):
    html = client.get("/characters").text
    assert "No characters yet" in html and "Add your first character" in html
    assert 'hx-post="/api/v1/bible/characters"' in html  # the add dialog


def test_campaign_page_saves_and_escapes(client):
    html = client.get("/characters/campaign").text
    assert 'name="setting"' in html and "Finnish" in html and "None set" in html
    form = {
        "setting": "Restov <b>",
        "tone": "kevyt",
        "bubble_language": "Finnish",
        "style_positive": "ink comic",
        "style_negative": "blurry",
        "style_page_look": "white balloons",
    }
    r = client.post("/api/v1/bible/campaign", data=form)
    b = bible.load()
    assert (b.setting, b.style.positive, b.style.negative) == ("Restov <b>", "ink comic", "blurry")
    assert b.style.page_look == "white balloons"
    assert "Restov &lt;b&gt;" in r.text and 'id="campaign"' in r.text
    assert toast(r) == {"message": "Campaign and style saved.", "level": "success"}


def test_cards_show_each_characters_readiness(client):
    cid = add(client, "Rintaro")
    html = client.get("/characters").text
    assert f'href="/characters/{cid}"' in html and "Rintaro" in html
    assert "0 / 5 set up" in html and "No height" in html and "0 ready for comics" in html

    upload(client, cid, "red")
    client.post(
        f"/api/v1/bible/characters/{cid}",
        data={"name": "Rintaro", "appearance": "seal", "height": "60", "height_field": "1"},
    )
    html = client.get("/characters").text
    assert ">Ready<" in html or "Ready</span>" in html
    assert "60 cm" in html and "1 image" in html and "1 ready for comics" in html
    assert f"/api/v1/bible/characters/{cid}/images/" in html and "?w=320" in html  # avatar


# --- one character's page --------------------------------------------------------------------


def test_characters_are_added_edited_and_deleted(client):
    r = client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    assert r.headers["HX-Redirect"] == "/characters/rintaro?notice=Added%20Rintaro."

    r = client.post(
        "/api/v1/bible/characters/rintaro",
        data={
            "name": "Rintaro",
            "aliases": "Rin, Uminari",
            "appearance": "red kimono",
            "traits": "spotted grey seal\nred headband\n",
        },
    )
    c = bible.load().characters[0]
    assert (c.aliases, c.appearance) == (["Rin", "Uminari"], "red kimono")
    assert c.traits == ["spotted grey seal", "red headband"]
    assert 'id="profile"' in r.text and "red kimono" in r.text
    assert 'id="char-summary"' in r.text and 'hx-swap-oob="true"' in r.text  # summary follows
    assert toast(r)["message"] == "Profile saved."

    html = client.get("/characters/rintaro").text
    assert "Rintaro" in html and "red kimono" in html and "Also: Rin, Uminari" in html

    r = client.post("/api/v1/bible/characters/rintaro/delete")
    assert r.headers["HX-Redirect"] == "/characters?notice=Deleted%20Rintaro."
    assert bible.load().characters == []


def test_an_empty_name_is_refused_without_changing_the_page(client):
    r = client.post("/api/v1/bible/characters", data={"name": "  "})
    assert r.headers["HX-Reswap"] == "none" and toast(r)["level"] == "error"
    assert "Name is required" in toast(r)["message"] and bible.load().characters == []


def test_a_character_cannot_take_a_page_address(client):
    assert add(client, "Campaign") == "campaign-2"
    assert client.get("/characters/campaign").status_code == 200  # still the style page


def test_unknown_character_is_404(client):
    assert client.get("/characters/nope").status_code == 404
    assert client.post("/api/v1/bible/characters/nope/delete").status_code == 404


def test_the_page_lists_what_is_still_missing(client):
    cid = add(client)
    html = client.get(f"/characters/{cid}").text
    for step in ("Reference image", "Height", "Description", "Approved sheet", "Detail sheet"):
        assert step in html
    assert "nothing yet: upload a reference image." in html
    assert 'href="#images"' in html and 'id="images"' in html  # the checklist links


# --- reference images ------------------------------------------------------------------------


def test_images_upload_serve_thumbnail_and_delete(client):
    cid = add(client)
    r = upload(client, cid, "red", "blue")
    names = bible.load().characters[0].images
    assert len(names) == 2 and toast(r)["message"] == "Uploaded 2 images."
    assert 'id="character-media"' in r.text and f"/images/{names[0]}?w=320" in r.text
    assert 'id="char-summary"' in r.text  # hero image and checklist follow

    full = client.get(f"/api/v1/bible/characters/{cid}/images/{names[0]}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/png"
    thumb = client.get(f"/api/v1/bible/characters/{cid}/images/{names[0]}?w=200")
    assert thumb.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/v1/bible/characters/{cid}/images/..%2Fbible.json").status_code == 404

    client.post(f"/api/v1/bible/characters/{cid}/images/{names[0]}/delete")
    assert bible.load().characters[0].images == names[1:]


def test_a_non_image_upload_shows_an_error(client):
    cid = add(client)
    files = {"files": ("notes.txt", b"hello", "text/plain")}
    r = client.post(f"/api/v1/bible/characters/{cid}/images", files=files)
    assert toast(r)["level"] == "error" and "not an image" in toast(r)["message"]
    assert bible.load().characters[0].images == []


def test_images_on_pages_are_marked_and_can_be_moved_first(client):
    cid = add(client)
    upload(client, cid, "red", "blue")
    first, second = bible.load().characters[0].images
    html = client.get(f"/characters/{cid}").text
    assert html.count("data-on-pages") == 1 and f"/images/{second}/first" in html
    assert f"/images/{first}/first" not in html  # already first
    r = client.post(f"/api/v1/bible/characters/{cid}/images/{second}/first")
    assert bible.load().characters[0].images == [second, first]
    assert "goes with every page" in toast(r)["message"]


def test_draft_description_from_images(client, monkeypatch):
    cid = add(client)
    upload(client, cid, "red")
    seen = {}

    def fake_describe(name, images, notes=""):
        seen.update(name=name, count=len(images), notes=notes)
        draft = CharacterDraft(appearance="tall swordsman, red <kimono>", traits=["red headband"])
        return draft, 900

    monkeypatch.setattr(api, "describe_character", fake_describe)
    html = client.post(
        f"/api/v1/bible/characters/{cid}/describe", data={"appearance": "carries a katana"}
    ).text
    assert seen == {"name": "Rintaro", "count": 1, "notes": "carries a katana"}
    assert "tall swordsman, red &lt;kimono&gt;" in html and "<textarea" in html
    assert 'name="traits"' in html and "red headband" in html
    assert bible.load().characters[0].appearance == ""  # a draft: saved only with Save
    assert bible.load().tokens_used == 900


def test_draft_needs_images(client):
    cid = add(client)
    html = client.post(f"/api/v1/bible/characters/{cid}/describe", data={}).text
    assert "Upload reference images first" in html


# --- character sheets ------------------------------------------------------------------------


def test_sheets_draw_approve_serve_and_delete(client, monkeypatch):
    cid = add(client)
    upload(client, cid, "red")
    seen = {}

    def fake_sheet(character, images, b):
        seen.update(name=character.name, count=len(images))
        return png("green", (1600, 900)), 5600

    monkeypatch.setattr(api, "draw_sheet", fake_sheet)
    r = client.post(f"/api/v1/bible/characters/{cid}/sheets")
    assert seen == {"name": "Rintaro", "count": 1}
    assert f"/api/v1/bible/characters/{cid}/sheets/sheet_1.png?w=640" in r.text
    assert "Approve it if it matches" in toast(r)["message"]
    assert bible.load().tokens_used == 5600
    thumb = client.get(f"/api/v1/bible/characters/{cid}/sheets/sheet_1.png?w=640")
    assert Image.open(io.BytesIO(thumb.content)).size == (640, 360)

    r = client.post(f"/api/v1/bible/characters/{cid}/sheets/sheet_1.png/approve")
    assert bible.load().character(cid).sheet == "sheet_1.png"
    assert "Approved · on pages" in r.text
    page = client.get(f"/characters/{cid}").text
    assert "/sheets/sheet_1.png?w=640" in page and "the approved sheet" in page  # hero

    client.post(f"/api/v1/bible/characters/{cid}/sheets/sheet_1.png/delete")
    assert bible.load().character(cid).sheets == []


def test_a_sheet_needs_reference_images(client, monkeypatch):
    cid = add(client)
    monkeypatch.setattr(api, "draw_sheet", lambda *a: (_ for _ in ()).throw(AssertionError))
    html = client.get(f"/characters/{cid}").text
    assert 'title="Upload reference images first"' in html  # the button is disabled
    r = client.post(f"/api/v1/bible/characters/{cid}/sheets")
    assert toast(r) == {"message": "Upload reference images first.", "level": "error"}


def test_a_gemini_failure_is_shown_not_a_500(client, monkeypatch):
    cid = add(client)
    upload(client, cid, "red")

    def broken(*args):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(api, "draw_sheet", broken)
    r = client.post(f"/api/v1/bible/characters/{cid}/sheets")
    assert r.status_code == 200 and "503 UNAVAILABLE" in toast(r)["message"]


def test_detail_sheet_needs_an_approved_sheet_and_uses_it(client, monkeypatch):
    cid = add(client, "Pentik")
    upload(client, cid, "red")
    seen = {}

    def fake_detail(character, sheet, images, b):
        seen.update(sheet=sheet, count=len(images))
        return png("green"), 6000

    monkeypatch.setattr(api, "draw_detail_sheet", fake_detail)
    r = client.post(f"/api/v1/bible/characters/{cid}/details")
    assert "Approve a character sheet first" in toast(r)["message"] and seen == {}

    bible.approve_sheet(cid, bible.add_sheet(cid, png("blue")))
    r = client.post(f"/api/v1/bible/characters/{cid}/details")
    assert seen == {"sheet": bible.sheet_path(cid, "sheet_1.png").read_bytes(), "count": 1}
    assert "detail_2.png" in r.text
    client.post(f"/api/v1/bible/characters/{cid}/sheets/detail_2.png/approve")
    assert bible.load().character(cid).detail == "detail_2.png"


# --- profile fields --------------------------------------------------------------------------


def test_never_list_is_edited_with_the_character(client):
    cid = add(client, "Pentik")
    form = {"name": "Pentik", "aliases": "", "appearance": "", "traits": "", "never": "a tabard\n"}
    client.post(f"/api/v1/bible/characters/{cid}", data=form)
    assert bible.load().character(cid).never == ["a tabard"]
    assert 'name="never"' in client.get(f"/characters/{cid}").text


def test_height_is_set_from_the_form_shown_and_validated(client):
    cid = add(client)
    form = {"name": "Rintaro", "aliases": "", "appearance": "seal", "height_field": "1"}
    client.post(f"/api/v1/bible/characters/{cid}", data={**form, "height": "0,6 m"})
    assert bible.load().characters[0].height_cm == 60
    assert 'name="height" value="60"' in client.get(f"/characters/{cid}").text

    r = client.post(f"/api/v1/bible/characters/{cid}", data={**form, "height": "tall"})
    assert r.headers["HX-Reswap"] == "none" and "Height must be a number" in toast(r)["message"]
    assert bible.load().characters[0].height_cm == 60  # a bad value changes nothing

    without = {k: v for k, v in form.items() if k != "height_field"}
    client.post(f"/api/v1/bible/characters/{cid}", data=without)  # e.g. an older form
    assert bible.load().characters[0].height_cm == 60

    client.post(f"/api/v1/bible/characters/{cid}", data={**form, "height": ""})  # cleared
    assert bible.load().characters[0].height_cm is None


# --- style reference -------------------------------------------------------------------------


def test_style_anchor_from_a_comic_page_shown_and_removed(client):
    from app import comics

    c = comics.create("ended1", "S")
    c.script = [comics.ScriptPage(title="T", panels=[comics.ScriptPanel(visual="v")])]
    comics.add_page_version(c, 0, png("white"))
    html = client.post(f"/api/v1/comics/{c.id}/pages/page_1_v1.png/anchor").text
    assert "Style reference set" in html
    assert bible.anchor_image() == comics.page_path(c.id, "page_1_v1.png").read_bytes()
    assert "/api/v1/bible/anchor" in client.get("/characters/campaign").text
    assert client.get("/api/v1/bible/anchor").status_code == 200
    r = client.post("/api/v1/bible/anchor/delete")
    assert "None set" in r.text and toast(r)["message"] == "Style reference removed."
    assert bible.anchor_image() is None
    assert client.get("/api/v1/bible/anchor").status_code == 404
