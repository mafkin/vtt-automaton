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


def test_ended_session_is_queued_once(client):
    r = client.post("/api/v1/comic/ended1")
    assert r.status_code == 202 and r.json()["status"] == "accepted"
    assert client.app.state.queue.jobs == {"comic:ended1": ("generate_comic", "ended1")}
    assert client.post("/api/v1/comic/ended1").json()["status"] == "already_queued"


def test_live_session_is_refused(client):
    r = client.post("/api/v1/comic/live1")
    assert r.status_code == 409
    assert client.app.state.queue.jobs == {}


def test_unknown_session(client):
    assert client.post("/api/v1/comic/nope").status_code == 404


def test_dashboard_sessions_escape_labels_and_hide_button_for_live(client):
    html = client.get("/api/v1/dashboard/sessions").text
    assert "Session &lt;b&gt;12&lt;/b&gt;" in html and "<b>12</b>" not in html
    assert "hx-post='/api/v1/comic/ended1'" in html
    assert "hx-post='/api/v1/comic/live1'" not in html
    assert "Live (stop it first)" in html


def test_dashboard_page_renders(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "hx-get" in r.text
