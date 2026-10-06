import json

import pytest
from fastapi.testclient import TestClient

from app.api import app


class FakeQueue:
    def __init__(self):
        self.jobs: dict[str, tuple] = {}

    async def enqueue_job(self, name, *args, _job_id=None):
        if _job_id in self.jobs:
            return None
        self.jobs[_job_id] = (name, *args)
        return object()


@pytest.fixture
def client(sessions_db):
    app.state.queue = FakeQueue()
    with TestClient(app) as c:
        yield c
    app.state.queue = None


@pytest.fixture
def idle_client(no_live_sessions, client):
    return client


def lock(path, session_id="ended1"):
    path.write_text(json.dumps({"session_id": session_id, "started_at": 0}))


def test_ended_session_is_queued_once(idle_client):
    client = idle_client
    r = client.post("/api/v1/comic/ended1")
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    assert client.app.state.queue.jobs == {"comic:ended1": ("generate_comic", "ended1", False)}
    assert client.post("/api/v1/comic/ended1").json()["status"] == "already_queued"


def test_live_session_is_refused(client):
    r = client.post("/api/v1/comic/live1")
    assert r.status_code == 409
    assert client.app.state.queue.jobs == {}


def test_any_live_session_blocks_a_comic(client):
    # ended1 is finished, but live1 is recording right now.
    r = client.post("/api/v1/comic/ended1")
    assert r.status_code == 409
    assert client.app.state.queue.jobs == {}


def test_a_running_comic_blocks_another(idle_client, comic_lock):
    lock(comic_lock)
    r = idle_client.post("/api/v1/comic/ended1")
    assert r.status_code == 409
    assert idle_client.app.state.queue.jobs == {}


def test_a_test_run_can_be_requested(idle_client):
    r = idle_client.post("/api/v1/comic/ended1?test=true")
    assert r.status_code == 202
    assert idle_client.app.state.queue.jobs == {"comic:ended1": ("generate_comic", "ended1", True)}


def test_dashboard_buttons_queue_a_full_or_a_test_run(idle_client):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    idle_client.post("/api/v1/dashboard/generate", content="session_id=ended1", headers=form)
    assert idle_client.app.state.queue.jobs["comic:ended1"] == ("generate_comic", "ended1", False)

    idle_client.app.state.queue.jobs.clear()
    body = "session_id=ended1&mode=test"
    html = idle_client.post("/api/v1/dashboard/generate", content=body, headers=form).text
    assert idle_client.app.state.queue.jobs["comic:ended1"] == ("generate_comic", "ended1", True)
    assert "Test run" in html


def test_unknown_session(idle_client):
    assert idle_client.post("/api/v1/comic/nope").status_code == 404


def test_dashboard_sessions_are_a_read_only_overview(client):
    html = client.get("/api/v1/dashboard/sessions").text
    assert "Session &lt;b&gt;12&lt;/b&gt;" in html and "<b>12</b>" not in html
    assert "hx-post" not in html
    assert "Live" in html


def test_picker_lists_finished_transcripts_and_can_generate(idle_client):
    html = idle_client.get("/api/v1/dashboard/picker").text
    assert "<option value='ended1'>" in html
    assert "Session &lt;b&gt;12&lt;/b&gt;" in html and "2 lines" in html
    assert "live1" not in html
    assert "hx-post" in html and " disabled>" not in html
    assert "Generate comic (8–10 pages)" in html
    assert "name='mode' value='test'" in html and "Test run (1–2 pages)" in html


def test_picker_is_disabled_while_a_session_is_live(client):
    html = client.get("/api/v1/dashboard/picker").text
    assert html.count(" disabled>") == 2 and "Session live" in html


def test_picker_is_disabled_during_a_comic(idle_client, comic_lock):
    lock(comic_lock)
    html = idle_client.get("/api/v1/dashboard/picker").text
    assert " disabled>" in html and "Comic in progress" in html


def test_picker_without_transcripts(idle_client, sessions_db):
    import sqlite3

    with sqlite3.connect(sessions_db) as conn:
        conn.execute("DELETE FROM segments")
    conn.close()
    html = idle_client.get("/api/v1/dashboard/picker").text
    assert "No finished sessions with a transcript yet" in html
    assert "hx-post" not in html


def test_mode_banner(idle_client, comic_lock):
    assert "Idle" in idle_client.get("/api/v1/dashboard/mode").text
    lock(comic_lock, "ended1")
    html = idle_client.get("/api/v1/dashboard/mode").text
    assert "Comic mode" in html and "ended1" in html and "transcription unavailable" in html


def test_transcript_preview_is_escaped_and_short(idle_client):
    html = idle_client.get("/api/v1/dashboard/transcript?session_id=ended1").text
    assert "GM: Örkit hyökkäävät." in html
    assert idle_client.get("/api/v1/dashboard/transcript?session_id=nope").status_code == 404


def test_dashboard_page_renders(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    for fragment in ("/api/v1/dashboard/mode", "/api/v1/dashboard/picker"):
        assert fragment in r.text
