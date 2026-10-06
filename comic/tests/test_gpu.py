import httpx
import pytest

from app.config import settings
from app.gpu import GpuHandoverError, gpu_lease


def docker(statuses: dict[str, int], calls: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(statuses.get(request.url.path.rsplit("/", 1)[1], 204))

    return httpx.AsyncClient(base_url="http://proxy", transport=httpx.MockTransport(handler))


async def test_stops_and_restarts_the_stt_container():
    calls: list[str] = []
    async with gpu_lease(docker({}, calls)):
        assert calls == ["/containers/vtt-stt-worker/stop"]
    assert calls[-1] == "/containers/vtt-stt-worker/start"


async def test_restarts_even_when_rendering_fails():
    calls: list[str] = []
    with pytest.raises(RuntimeError):
        async with gpu_lease(docker({}, calls)):
            raise RuntimeError("ComfyUI crashed")
    assert calls[-1] == "/containers/vtt-stt-worker/start"


async def test_a_failed_stop_aborts_before_rendering():
    # e.g. a wrong container name: 404. Rendering anyway would put SDXL and Whisper on one GPU.
    calls: list[str] = []
    rendered = False
    with pytest.raises(GpuHandoverError, match="404"):
        async with gpu_lease(docker({"stop": 404}, calls)):
            rendered = True
    assert not rendered
    assert calls == ["/containers/vtt-stt-worker/stop"]


async def test_already_stopped_is_fine():
    calls: list[str] = []
    async with gpu_lease(docker({"stop": 304, "start": 304}, calls)):
        pass
    assert len(calls) == 2


async def test_no_handover_without_a_container_name(monkeypatch):
    monkeypatch.setattr(settings, "stt_container_name", "")
    calls: list[str] = []
    async with gpu_lease(docker({}, calls)):
        pass
    assert calls == []
