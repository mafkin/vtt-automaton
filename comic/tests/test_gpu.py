import json

import httpx
import pytest

from app import gpu
from app.config import settings
from app.gpu import GpuHandoverError, comic_mode, lock_info, recover

STT = "/containers/vtt-stt-worker"
COMFY = "/containers/vtt-comfyui"


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(gpu, "_POLL_SECONDS", 0)
    monkeypatch.setattr(settings, "comfyui_start_timeout", 0.05)


def docker(calls: list[str], statuses: dict[str, int] | None = None) -> httpx.AsyncClient:
    """Fake docker-proxy. statuses maps a path such as "/containers/x/stop" to an HTTP status."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response((statuses or {}).get(request.url.path, 204))

    return httpx.AsyncClient(base_url="http://proxy", transport=httpx.MockTransport(handler))


def comfy(calls: list[str], ready_after: int = 0) -> httpx.AsyncClient:
    """Fake ComfyUI that answers /system_stats after `ready_after` refused attempts."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts <= ready_after:
            raise httpx.ConnectError("not up yet")
        calls.append("comfy ready")
        return httpx.Response(200, json={})

    return httpx.AsyncClient(base_url="http://comfy", transport=httpx.MockTransport(handler))


async def test_hands_the_gpu_over_and_back_in_order():
    calls: list[str] = []
    async with comic_mode("s1", docker(calls), comfy(calls, ready_after=2)):
        assert calls == [f"{STT}/stop", f"{COMFY}/start", "comfy ready"]
        assert lock_info()["session_id"] == "s1"
        calls.append("render")
    assert calls[-2:] == [f"{COMFY}/stop", f"{STT}/start"]
    assert lock_info() is None


async def test_gives_the_gpu_back_when_rendering_fails():
    calls: list[str] = []
    with pytest.raises(RuntimeError):
        async with comic_mode("s1", docker(calls), comfy(calls)):
            raise RuntimeError("panel failed")
    assert calls[-2:] == [f"{COMFY}/stop", f"{STT}/start"]
    assert lock_info() is None


async def test_a_failed_stt_stop_aborts_before_comfyui_starts():
    # e.g. a wrong container name: 404. Starting ComfyUI anyway would put SDXL and Whisper on
    # one GPU.
    calls: list[str] = []
    rendered = False
    with pytest.raises(GpuHandoverError, match="404"):
        async with comic_mode("s1", docker(calls, {f"{STT}/stop": 404}), comfy(calls)):
            rendered = True
    assert not rendered
    assert f"{COMFY}/start" not in calls
    assert lock_info() is None


async def test_comfyui_that_never_comes_up_is_stopped_and_stt_restarted():
    calls: list[str] = []
    rendered = False
    with pytest.raises(GpuHandoverError, match="ComfyUI"):
        async with comic_mode("s1", docker(calls), comfy(calls, ready_after=10**9)):
            rendered = True
    assert not rendered
    assert calls[-2:] == [f"{COMFY}/stop", f"{STT}/start"]
    assert lock_info() is None


async def test_already_stopped_or_started_is_fine():
    calls: list[str] = []
    not_modified = {f"{STT}/stop": 304, f"{COMFY}/start": 304, f"{COMFY}/stop": 304}
    async with comic_mode("s1", docker(calls, not_modified), comfy(calls)):
        pass
    assert calls[-1] == f"{STT}/start"


async def test_without_stt_only_comfyui_is_started_and_stopped(monkeypatch):
    monkeypatch.setattr(settings, "stt_container_name", "")
    calls: list[str] = []
    async with comic_mode("s1", docker(calls), comfy(calls)):
        pass
    assert calls == [f"{COMFY}/start", "comfy ready", f"{COMFY}/stop"]


async def test_recover_returns_the_server_to_idle(comic_lock):
    # The worker died mid-job: lock left behind, ComfyUI running, STT stopped.
    comic_lock.write_text(json.dumps({"session_id": "s1", "started_at": 0}))
    calls: list[str] = []
    await recover({}, docker(calls))
    assert calls == [f"{COMFY}/stop", f"{STT}/start"]
    assert lock_info() is None


def test_lock_info_ignores_a_corrupt_lock(comic_lock):
    comic_lock.write_text("not json")
    assert lock_info() == {"session_id": None, "started_at": None}
