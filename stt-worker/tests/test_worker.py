import io
import json
import time
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, wav_duration
from app.transcriber import Piece, build_prompt, clean_transcript

TOKEN = "stt-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_wav(seconds: float, rate: int = 48000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeTranscriber:
    def __init__(self, text="Nethys, mitä Trip tekee?"):
        self.text = text
        self.calls = 0

    def transcribe(self, wav: bytes) -> str:
        self.calls += 1
        return self.text


class FakeBackend:
    def __init__(self, statuses=(200,)):
        self.statuses = list(statuses)
        self.received = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.received.append(json.loads(request.content))
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return httpx.Response(status, json={})

    def client(self):
        return httpx.AsyncClient(
            base_url="http://backend", transport=httpx.MockTransport(self.handler)
        )


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def make_client(transcriber, backend, **settings):
    app = create_app(
        Settings(token=TOKEN, backend_token="b", **settings),
        transcriber=transcriber,
        backend=backend.client(),
    )
    return TestClient(app)


PARAMS = {"session_id": "s1", "speaker": "Aino", "speaker_id": "123", "t_start": 1000.0}


def test_utterance_is_transcribed_and_forwarded():
    backend = FakeBackend()
    with make_client(FakeTranscriber(), backend) as client:
        params = {**PARAMS, "character": "Valeros"}
        r = client.post("/v1/utterances", params=params, content=make_wav(2.5), headers=AUTH)
        assert r.status_code == 202 and r.json() == {"queued": True}
        assert wait_for(lambda: backend.received)
    assert backend.received == [
        {
            "session_id": "s1",
            "speaker": "Aino",
            "speaker_id": "123",
            "character": "Valeros",
            "t_start": 1000.0,
            "t": 1002.5,
            "text": "Nethys, mitä Trip tekee?",
        }
    ]


def test_empty_transcripts_are_not_forwarded():
    backend = FakeBackend()
    transcriber = FakeTranscriber(text="")
    with make_client(transcriber, backend) as client:
        client.post("/v1/utterances", params=PARAMS, content=make_wav(1), headers=AUTH)
        assert wait_for(lambda: transcriber.calls == 1)
        assert wait_for(lambda: client.get("/healthz").json()["processed"] == 1)
    assert backend.received == []


def test_short_clips_are_skipped():
    transcriber = FakeTranscriber()
    with make_client(transcriber, FakeBackend()) as client:
        r = client.post("/v1/utterances", params=PARAMS, content=make_wav(0.2), headers=AUTH)
        assert r.json() == {"queued": False, "reason": "too short"}
    assert transcriber.calls == 0


def test_backend_errors_are_retried(monkeypatch):
    monkeypatch.setattr("app.pipeline._RETRY_DELAYS", (0.01, 0.01))
    backend = FakeBackend(statuses=(503, 200))
    with make_client(FakeTranscriber(), backend) as client:
        client.post("/v1/utterances", params=PARAMS, content=make_wav(1), headers=AUTH)
        assert wait_for(lambda: len(backend.received) == 2)


def test_auth_and_bad_input():
    with make_client(FakeTranscriber(), FakeBackend()) as client:
        r = client.post("/v1/utterances", params=PARAMS, content=make_wav(1))
        assert r.status_code == 401
        r = client.post("/v1/utterances", params=PARAMS, content=b"not audio", headers=AUTH)
        assert r.status_code == 400
        r = client.post(
            "/v1/utterances", params={"speaker": "x"}, content=make_wav(1), headers=AUTH
        )
        assert r.status_code == 422


def test_full_queue_returns_503():
    class Blocking(FakeTranscriber):
        def transcribe(self, wav):
            time.sleep(0.5)
            return ""

    with make_client(Blocking(), FakeBackend(), max_queue=1) as client:
        codes = [
            client.post(
                "/v1/utterances", params=PARAMS, content=make_wav(1), headers=AUTH
            ).status_code
            for _ in range(4)
        ]
    assert 503 in codes and codes[0] == 202


def test_requires_token_setting():
    with pytest.raises(RuntimeError):
        create_app(Settings(token=""), transcriber=FakeTranscriber())


def test_wav_duration():
    assert wav_duration(make_wav(1.5)) == pytest.approx(1.5)
    # ffmpeg-style file with an extra LIST chunk before "data"
    plain = make_wav(1.0)
    extra = b"LIST" + (12).to_bytes(4, "little") + b"INFOISFT\x00\x00\x00\x00"
    with_list = plain[:12] + plain[12:36] + extra + plain[36:]
    with_list = with_list[:4] + (len(with_list) - 8).to_bytes(4, "little") + with_list[8:]
    assert wav_duration(with_list) == pytest.approx(1.0)
    assert wav_duration(make_wav(2, rate=16000)) == pytest.approx(2)
    with pytest.raises(ValueError):
        wav_duration(b"RIFF....nope")


@pytest.mark.parametrize(
    "text",
    [
        "Kiitos katsomisesta!",
        "Kiitos.",
        "Suomenkieliset tekstit: Joku",
        "Tekstitys: Yle",
        "Thanks for watching!",
        "...",
        "   ",
    ],
)
def test_hallucinations_are_dropped(text):
    assert clean_transcript([Piece(text)]) == ""


def test_real_speech_is_kept():
    pieces = [Piece(" Nethys, kiitos kun autat."), Piece(" Voinko tehdä Tripin?")]
    assert clean_transcript(pieces) == "Nethys, kiitos kun autat. Voinko tehdä Tripin?"


def test_low_confidence_noise_pieces_are_dropped():
    pieces = [Piece("Hyökkään örkkiä.", 0.1, -0.3), Piece("öö mm", 0.9, -1.5)]
    assert clean_transcript(pieces) == "Hyökkään örkkiä."


def test_prompt_echo_is_dropped():
    prompt = build_prompt(["Nethys", "Trip"])
    assert clean_transcript([Piece("Pathfinder-roolipeli suomeksi.")], prompt) == ""
    assert clean_transcript([Piece(" Nethys, Trip.")], prompt) == ""
    kept = clean_transcript([Piece("Nethys, voinko tehdä Tripin?")], prompt)
    assert kept == "Nethys, voinko tehdä Tripin?"


def test_prompt_lists_terms():
    assert build_prompt(["Nethys", "Trip"]) == (
        "Pathfinder-roolipeli suomeksi. Sanastoa: Nethys, Trip."
    )
