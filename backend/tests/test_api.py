import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.fake import FakeProvider
from app.main import create_app
from app.rules.service import Citation, QueryAnalysis, RulingDraft

TOKEN = "test-token"


def respond(system, prompt, schema):
    if schema is QueryAnalysis:
        return QueryAnalysis(search_terms=["Trip"], question_en="Trip?")
    return RulingDraft(
        citations=[Citation(entry_id="Actions.aspx?ID=1", quote="lands prone.")],
        interpretation="Kohde on <b>prone</b>.",
        confidence="high",
    )


@pytest.fixture
def client(rules_db):
    settings = Settings(
        rules_db_path=rules_db,
        sessions_db_path=rules_db.parent / "sessions.db",
        client_tokens=[TOKEN],
        llm_provider="fake",
        rules_refresh_hours=0,
    )
    return TestClient(create_app(settings, llm=FakeProvider(respond)))


def auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_requires_token(client):
    assert client.post("/api/v1/rulings", json={"query": "Trip"}).status_code == 401
    r = client.post("/api/v1/rulings", json={"query": "Trip"}, headers=auth("wrong"))
    assert r.status_code == 401


def test_ruling_with_foundry_html(client):
    r = client.post("/api/v1/rulings", json={"query": "Trip", "render": "foundry"}, headers=auth())
    assert r.status_code == 200
    body = r.json()
    ref = body["ruling"]["raw"][0]
    assert ref["quote"] == "lands prone."
    assert ref["text"].startswith("Fixture: Attempt an Athletics check")
    html = body["html"]
    assert "&lt;b&gt;prone&lt;/b&gt;" in html  # LLM output is escaped
    # Full rules as written come before the ruling.
    assert html.index("Fixture: Attempt an Athletics check") < html.index("Tulkinta")


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_app_refuses_to_start_without_tokens(rules_db):
    settings = Settings(
        rules_db_path=rules_db, sessions_db_path=rules_db.parent / "s.db", client_tokens=[]
    )
    with pytest.raises(RuntimeError):
        create_app(settings, llm=FakeProvider(respond))


def test_ruling_without_render_returns_data_only(client):
    r = client.post("/api/v1/rulings", json={"query": "Trip"}, headers=auth())
    assert r.json()["html"] is None
