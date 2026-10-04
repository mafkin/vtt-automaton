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
    settings = Settings(rules_db_path=rules_db, client_tokens=[TOKEN], llm_provider="fake")
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
    assert body["ruling"]["raw"][0]["quote"] == "lands prone."
    assert "&lt;b&gt;prone&lt;/b&gt;" in body["html"]  # LLM output is escaped
    assert body["embed"] is None


def test_ruling_with_discord_embed(client):
    r = client.post("/api/v1/rulings", json={"query": "Trip", "render": "discord"}, headers=auth())
    embed = r.json()["embed"]
    assert embed["fields"][0]["name"] == "Trip (action)"
    assert "aonprd.com" in embed["fields"][0]["value"]


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_app_refuses_to_start_without_tokens(rules_db):
    with pytest.raises(RuntimeError):
        create_app(Settings(rules_db_path=rules_db, client_tokens=[]), llm=FakeProvider(respond))
