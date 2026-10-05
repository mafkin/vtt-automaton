import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sessions.store import SessionStore, StoredSegment, format_transcript
from tests.test_voice import make_llm

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(rules_db):
    settings = Settings(
        rules_db_path=rules_db,
        sessions_db_path=rules_db.parent / "sessions.db",
        client_tokens=[TOKEN],
        llm_provider="fake",
        rules_refresh_hours=0,
    )
    return TestClient(create_app(settings, llm=make_llm()))


def segment(session_id, speaker, text, t, **extra):
    return {"session_id": session_id, "speaker": speaker, "text": text, "t": t, **extra}


def test_session_lifecycle_and_transcript(client):
    r = client.post("/api/v1/sessions", json={"label": "Session 12"}, headers=AUTH)
    assert r.status_code == 201
    session = r.json()
    sid = session["id"]
    start = session["started_at"]

    for body in [
        segment(sid, "GM", "Örkki juoksee ohi.", start + 65, t_start=start + 62),
        segment(sid, "Aino", "Hyökkään!", start + 70, t_start=start + 69, character="Valeros"),
    ]:
        r = client.post("/api/v1/transcript/segments", json=body, headers=AUTH)
        assert r.json() == {"ruling_id": None, "stored": True}

    stopped = client.post(f"/api/v1/sessions/{sid}/stop", headers=AUTH).json()
    assert stopped["ended_at"] is not None and stopped["segment_count"] == 2

    # Late results from the STT worker after stop are still kept.
    late = segment(sid, "Ville", "Hups.", start + 80, t_start=start + 79)
    assert client.post("/api/v1/transcript/segments", json=late, headers=AUTH).json()["stored"]

    segments = client.get(f"/api/v1/sessions/{sid}/transcript", headers=AUTH).json()
    assert [s["speaker"] for s in segments] == ["GM", "Aino", "Ville"]
    text = client.get(f"/api/v1/sessions/{sid}/transcript?format=text", headers=AUTH).text
    assert "[0:01:09] Aino (Valeros): Hyökkään!" in text
    assert [s["id"] for s in client.get("/api/v1/sessions", headers=AUTH).json()] == [sid]


def test_unknown_session(client):
    assert client.get("/api/v1/sessions/nope", headers=AUTH).status_code == 404
    assert client.post("/api/v1/sessions/nope/stop", headers=AUTH).status_code == 404
    r = client.post("/api/v1/transcript/segments", json=segment("nope", "A", "x", 1), headers=AUTH)
    assert r.status_code == 404


def test_sessions_require_token(client):
    assert client.post("/api/v1/sessions", json={}).status_code == 401


def test_spoken_question_in_a_session_uses_character_in_context(client, rules_db):
    sid = client.post("/api/v1/sessions", json={}, headers=AUTH).json()["id"]
    service = client.app.state.voice_service
    service._hub.add(object())  # pretend a GM is connected; broadcast failures are ignored
    client.post(
        "/api/v1/transcript/segments",
        json=segment(sid, "Aino", "Juoksen ohi.", 100, character="Valeros"),
        headers=AUTH,
    )
    assert service._context_before(101) == ["Aino (Valeros): Juoksen ohi."]


def test_store_orders_by_start_time(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    session = store.start("x")
    store.add_segment(session.id, StoredSegment(speaker="B", speaker_id=None, character=None,
                                                t_start=20, t_end=25, text="second"))  # fmt: skip
    store.add_segment(session.id, StoredSegment(speaker="A", speaker_id=None, character=None,
                                                t_start=10, t_end=30, text="first"))  # fmt: skip
    assert [s.text for s in store.transcript(session.id)] == ["first", "second"]
    assert format_transcript(store.get(session.id), []).startswith("# x (")
