import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import api, bible
from app.api import app


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
    }
    html = client.post("/api/v1/bible/campaign", data=form).text
    b = bible.load()
    assert (b.setting, b.style.positive, b.style.negative) == ("Restov <b>", "ink comic", "blurry")
    assert "Restov &lt;b&gt;" in html and "Saved" in html


def test_characters_can_be_added_edited_and_deleted(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    client.post(
        "/api/v1/bible/characters/rintaro",
        data={"name": "Rintaro", "aliases": "Rin, Uminari", "appearance": "red kimono"},
    )
    c = bible.load().characters[0]
    assert (c.aliases, c.appearance) == (["Rin", "Uminari"], "red kimono")
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
        return "tall swordsman, red <kimono>"

    monkeypatch.setattr(api, "describe_character", fake_describe)
    html = client.post(
        "/api/v1/bible/characters/rintaro/describe", data={"appearance": "carries a katana"}
    ).text
    assert seen == {"name": "Rintaro", "count": 1, "notes": "carries a katana"}
    assert "tall swordsman, red &lt;kimono&gt;" in html and "<textarea" in html
    assert bible.load().characters[0].appearance == ""  # a draft: saved only with Save


def test_draft_needs_images(client):
    client.post("/api/v1/bible/characters", data={"name": "Rintaro"})
    html = client.post("/api/v1/bible/characters/rintaro/describe", data={}).text
    assert "Upload reference images first" in html


def test_dashboard_has_the_bible_card(client):
    assert "/api/v1/bible" in client.get("/dashboard").text
