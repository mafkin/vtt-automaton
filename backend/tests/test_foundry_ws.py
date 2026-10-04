import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from tests.test_voice import make_llm

TOKEN = "test-token"
ORIGIN = "https://world.example"


@pytest.fixture
def client(rules_db):
    settings = Settings(
        rules_db_path=rules_db,
        client_tokens=[TOKEN],
        cors_origins=[ORIGIN],
        llm_provider="fake",
        rules_refresh_hours=0,
    )
    return TestClient(create_app(settings, llm=make_llm()))


def connect(client, origin=ORIGIN):
    return client.websocket_connect("/ws/foundry", headers={"origin": origin})


def hello(ws, token=TOKEN):
    ws.send_json({"type": "hello", "v": 1, "token": token})
    return ws.receive_json()


def test_chat_ruling_round_trip(client):
    with connect(client) as ws:
        assert hello(ws) == {"type": "welcome", "v": 1}
        ws.send_json(
            {
                "type": "ruling.request",
                "id": "r1",
                "request": {"query": "Trip", "render": "foundry"},
            }
        )
        assert ws.receive_json() == {"type": "ruling.pending", "id": "r1", "origin": "chat"}
        result = ws.receive_json()
        assert result["type"] == "ruling.result" and result["id"] == "r1"
        assert result["ruling"]["raw"][0]["name"] == "Trip"
        assert "vtt-arbiter" in result["html"]


def test_bad_request_and_ping(client):
    with connect(client) as ws:
        hello(ws)
        ws.send_json({"type": "ruling.request", "id": "r2", "request": {"query": ""}})
        error = ws.receive_json()
        assert error["type"] == "ruling.error" and error["code"] == "bad_request"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_wrong_token_is_rejected(client):
    with connect(client) as ws:
        assert hello(ws, token="nope") == {"type": "error", "code": "auth.invalid"}
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_foreign_origin_is_rejected(client):
    with pytest.raises(WebSocketDisconnect), connect(client, origin="https://evil.example") as ws:
        ws.receive_json()


def test_spoken_question_is_pushed_to_connected_gm(client):
    with connect(client) as ws:
        hello(ws)
        r = client.post(
            "/api/v1/transcript/segments",
            json={"speaker": "Aino", "text": "Nethys, mitä Trip tekee?"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        ruling_id = r.json()["ruling_id"]
        assert ruling_id
        pending = ws.receive_json()
        assert pending["type"] == "ruling.pending" and pending["origin"] == "voice"
        assert pending["query"] == "mitä Trip tekee?"
        result = ws.receive_json()
        assert result["type"] == "ruling.result" and result["id"] == ruling_id


def test_segment_without_wake_word(client):
    r = client.post(
        "/api/v1/transcript/segments",
        json={"speaker": "Aino", "text": "Hyökkään örkkiä."},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.json() == {"ruling_id": None}
