import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import api, bible
from app.api import app
from app.llm import CharacterDraft


@pytest.fixture
def client(sessions_db):
    app.state.queue = object()  # bible endpoints never queue jobs
    with TestClient(app) as c:
        yield c
    app.state.queue = None


def png(colour="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buf, "PNG")
    return buf.getvalue()


def test_the_bible_card_renders_empty(client):
    html = client.get("/api/v1/bible").text
    assert 'name="setting"' in html and "Finnish" in html
    assert "No characters yet" in html
    assert "only the character" in html  # other people in a reference picture leak into pages


def test_campaign_fields_are_saved_and_escaped(client):
    form = {
        "setting": "Restov <b>",
        "tone": "kevyt",
        "bubble_language": "Finnish",
        "style_positive": "ink comic",
        "style_negative": "blurry",
        "style_page_look": "white balloons",
    }
    html = client.post("/api/v1/bible/campaign", data=form).text
    b = bible.load()
    assert (b.setting, b.style.positive, b.style.negative) == ("Restov <b>", "ink comic", "blurry")
    assert b.style.page_look == "white balloons"
    assert "Restov &lt;b&gt;" in html and "Saved" in html


def test_characters_can_be_added_edited_and_deleted(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    client.post(
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
    html = client.get("/api/v1/bible").text
    assert "Rintaro" in html and "red kimono" in html
    client.post("/api/v1/bible/characters/rintaro/delete")
    assert bible.load().characters == []


def test_an_empty_name_is_refused(client):
    r = client.post("/api/v1/bible/characters", data={"name": "  "})
    assert "Name is required" in r.text and bible.load().characters == []


def test_image_upload_serve_and_delete(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    files = [
        ("files", ("a.png", png(), "image/png")),
        ("files", ("b.png", png("blue"), "image/png")),
    ]
    html = client.post("/api/v1/bible/characters/rintaro/images", files=files).text
    names = bible.load().characters[0].images
    assert len(names) == 2 and f"/api/v1/bible/characters/rintaro/images/{names[0]}" in html

    r = client.get(f"/api/v1/bible/characters/rintaro/images/{names[0]}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get("/api/v1/bible/characters/rintaro/images/..%2Fbible.json").status_code == 404

    client.post(f"/api/v1/bible/characters/rintaro/images/{names[0]}/delete")
    assert bible.load().characters[0].images == names[1:]


def test_a_non_image_upload_shows_an_error(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    files = {"files": ("notes.txt", b"hello", "text/plain")}
    html = client.post("/api/v1/bible/characters/rintaro/images", files=files).text
    assert "not an image" in html and bible.load().characters[0].images == []


def test_unknown_character_is_404(client):
    assert client.post("/api/v1/bible/characters/nope/delete").status_code == 404


def test_draft_description_from_images(client, monkeypatch):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    client.post(
        "/api/v1/bible/characters/rintaro/images",
        files={"files": ("a.png", png(), "image/png")},
    )
    seen = {}

    def fake_describe(name, images, notes=""):
        seen.update(name=name, count=len(images), notes=notes)
        draft = CharacterDraft(appearance="tall swordsman, red <kimono>", traits=["red headband"])
        return draft, 900

    monkeypatch.setattr(api, "describe_character", fake_describe)
    html = client.post(
        "/api/v1/bible/characters/rintaro/describe", data={"appearance": "carries a katana"}
    ).text
    assert seen == {"name": "Rintaro", "count": 1, "notes": "carries a katana"}
    assert "tall swordsman, red &lt;kimono&gt;" in html and "<textarea" in html
    assert 'name="traits"' in html and "red headband" in html
    assert bible.load().characters[0].appearance == ""  # a draft: saved only with Save
    assert bible.load().tokens_used == 900


def test_draft_needs_images(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    html = client.post("/api/v1/bible/characters/rintaro/describe", data={}).text
    assert "Upload reference images first" in html


def test_dashboard_has_the_bible_card(client):
    assert "/api/v1/bible" in client.get("/dashboard").text


def test_sheets_draw_approve_serve_and_delete(client, monkeypatch):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    client.post(
        "/api/v1/bible/characters/rintaro/images", files={"files": ("a.png", png(), "image/png")}
    )
    seen = {}

    def fake_sheet(character, images, b):
        seen.update(name=character.name, count=len(images))
        return png("green"), 5600

    monkeypatch.setattr(api, "draw_sheet", fake_sheet)
    html = client.post("/api/v1/bible/characters/rintaro/sheets").text
    assert seen == {"name": "Rintaro", "count": 1}
    assert "/api/v1/bible/characters/rintaro/sheets/sheet_1.png" in html
    assert bible.load().tokens_used == 5600
    assert client.get("/api/v1/bible/characters/rintaro/sheets/sheet_1.png").status_code == 200

    client.post("/api/v1/bible/characters/rintaro/sheets/sheet_1.png/approve")
    assert bible.load().character("rintaro").sheet == "sheet_1.png"
    assert "Approved" in client.get("/api/v1/bible").text
    client.post("/api/v1/bible/characters/rintaro/sheets/sheet_1.png/delete")
    assert bible.load().character("rintaro").sheets == []


def test_a_sheet_needs_reference_images(client, monkeypatch):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    monkeypatch.setattr(api, "draw_sheet", lambda *a: (_ for _ in ()).throw(AssertionError))
    html = client.post("/api/v1/bible/characters/rintaro/sheets").text
    assert "Upload reference images first" in html


def test_style_anchor_from_a_comic_page_shown_and_removed(client):
    from app import comics

    c = comics.create("ended1", "S")
    c.script = [comics.ScriptPage(title="T", panels=[comics.ScriptPanel(visual="v")])]
    comics.add_page_version(c, 0, png("white"))
    html = client.post(f"/api/v1/comics/{c.id}/pages/page_1_v1.png/anchor").text
    assert "Style reference set" in html
    assert bible.anchor_image() == comics.page_path(c.id, "page_1_v1.png").read_bytes()
    assert "/api/v1/bible/anchor" in client.get("/api/v1/bible").text
    assert client.get("/api/v1/bible/anchor").status_code == 200
    client.post("/api/v1/bible/anchor/delete")
    assert bible.anchor_image() is None
    assert client.get("/api/v1/bible/anchor").status_code == 404
