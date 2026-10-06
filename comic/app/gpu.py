"""Comic mode: the GPU is handed from speech-to-text to ComfyUI for one comic, then back.

Between comics ComfyUI isn't running and the STT worker holds the GPU. A comic stops STT,
starts ComfyUI, renders, then stops ComfyUI (which releases its VRAM) and starts STT again.
While that happens a lock file tells the backend to refuse new sessions.

Only ever between sessions: the API and the worker refuse to run while any session is live.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# Docker answers 204 when it stopped/started the container and 304 when it already was.
_OK = (204, 304)
_POLL_SECONDS = 2.0


class GpuHandoverError(RuntimeError):
    pass


def lock_info() -> dict | None:
    """The running comic ({"session_id", "started_at"}), or None when the server is idle."""
    path = Path(settings.lock_path)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # Unreadable, but there: still treat the GPU as taken.
        return {"session_id": None, "started_at": None}


def _write_lock(session_id: str) -> None:
    Path(settings.lock_path).write_text(
        json.dumps({"session_id": session_id, "started_at": time.time()})
    )


def _remove_lock() -> None:
    Path(settings.lock_path).unlink(missing_ok=True)


async def _container_action(client: httpx.AsyncClient, name: str, action: str) -> None:
    response = await client.post(f"/containers/{name}/{action}", timeout=60.0)
    if response.status_code not in _OK:
        raise GpuHandoverError(
            f"Could not {action} container {name!r}: HTTP {response.status_code} {response.text}"
        )


async def _wait_until_ready(comfy: httpx.AsyncClient) -> None:
    deadline = time.monotonic() + settings.comfyui_start_timeout
    while True:
        try:
            if (await comfy.get("/system_stats", timeout=5.0)).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            raise GpuHandoverError(
                f"ComfyUI did not answer within {settings.comfyui_start_timeout:g} s"
            )
        await asyncio.sleep(_POLL_SECONDS)


async def _give_back(docker: httpx.AsyncClient) -> None:
    """Stop ComfyUI and start STT. Never raises: each step is tried even if the other fails."""
    for name, action in (
        (settings.comfyui_container_name, "stop"),
        (settings.stt_container_name, "start"),
    ):
        if not name:
            continue
        try:
            log.info("Comic mode ending: %s %s", action, name)
            await _container_action(docker, name, action)
        except (httpx.HTTPError, GpuHandoverError):
            log.exception(
                "Could not %s %s; do it by hand: docker %s %s", action, name, action, name
            )


@asynccontextmanager
async def comic_mode(
    session_id: str,
    docker: httpx.AsyncClient | None = None,
    comfy: httpx.AsyncClient | None = None,
) -> AsyncIterator[None]:
    """Take the GPU for one comic and give it back afterwards, even if rendering fails.

    If STT can't be stopped, nothing is started: ComfyUI and Whisper together don't fit.
    """
    owned: list[httpx.AsyncClient] = []
    if docker is None:
        docker = httpx.AsyncClient(base_url=settings.docker_proxy_url)
        owned.append(docker)
    if comfy is None:
        comfy = httpx.AsyncClient(base_url=settings.comfyui_url)
        owned.append(comfy)
    _write_lock(session_id)
    try:
        if settings.stt_container_name:
            log.info("Comic mode: stopping %s to free the GPU", settings.stt_container_name)
            await _container_action(docker, settings.stt_container_name, "stop")
        try:
            log.info("Comic mode: starting %s", settings.comfyui_container_name)
            await _container_action(docker, settings.comfyui_container_name, "start")
            await _wait_until_ready(comfy)
            yield
        finally:
            await _give_back(docker)
    finally:
        _remove_lock()
        for client in owned:
            await client.aclose()


async def recover(ctx: dict | None = None, docker: httpx.AsyncClient | None = None) -> None:
    """Worker startup: undo a comic that was cut short (crash, restart, compose up)."""
    if lock_info() is not None:
        log.warning("Found a comic lock from an interrupted job; returning the GPU to STT")
    own = docker is None
    docker = docker or httpx.AsyncClient(base_url=settings.docker_proxy_url)
    try:
        await _give_back(docker)
    finally:
        _remove_lock()
        if own:
            await docker.aclose()
