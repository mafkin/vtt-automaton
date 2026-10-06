"""GPU handover: the speech-to-text worker holds the GPU, so it is stopped while ComfyUI renders.

Only ever between sessions: the API and the worker refuse to run for a live session.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# Docker answers 204 when it stopped/started the container and 304 when it already was.
_OK = (204, 304)


class GpuHandoverError(RuntimeError):
    pass


async def _container_action(client: httpx.AsyncClient, name: str, action: str) -> None:
    response = await client.post(f"/containers/{name}/{action}", timeout=60.0)
    if response.status_code not in _OK:
        raise GpuHandoverError(
            f"Could not {action} container {name!r}: HTTP {response.status_code} {response.text}"
        )


@asynccontextmanager
async def gpu_lease(client: httpx.AsyncClient | None = None) -> AsyncIterator[None]:
    """Stop the STT container, yield, and start it again even if rendering fails.

    If stopping fails, nothing is rendered: ComfyUI and Whisper together don't fit on the GPU.
    """
    name = settings.stt_container_name
    if not name:
        yield
        return
    own_client = client is None
    client = client or httpx.AsyncClient(base_url=settings.docker_proxy_url)
    try:
        log.info("Stopping %s to free the GPU", name)
        await _container_action(client, name, "stop")
        try:
            yield
        finally:
            log.info("Starting %s again", name)
            try:
                await _container_action(client, name, "start")
            except (httpx.HTTPError, GpuHandoverError):
                log.exception("Could not restart %s; start it by hand: docker start %s", name, name)
    finally:
        if own_client:
            await client.aclose()
