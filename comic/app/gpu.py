import httpx
import logging
import asyncio
from contextlib import asynccontextmanager
from app.config import settings

logger = logging.getLogger(__name__)

@asynccontextmanager
async def gpu_lease():
    """
    Acquires the GPU by pausing the STT worker.
    Restarts the STT worker when done.
    """
    logger.info(f"Stopping STT container: {settings.stt_container_name}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{settings.docker_proxy_url}/containers/{settings.stt_container_name}/stop", timeout=30.0)
            if r.status_code not in (204, 304):
                logger.warning(f"Stop STT returned {r.status_code}: {r.text}")
        except httpx.HTTPError as e:
            logger.error(f"Failed to stop STT container: {e}")
            raise

    try:
        yield
    finally:
        logger.info(f"Starting STT container: {settings.stt_container_name}")
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(f"{settings.docker_proxy_url}/containers/{settings.stt_container_name}/start", timeout=30.0)
                if r.status_code not in (204, 304):
                    logger.warning(f"Start STT returned {r.status_code}: {r.text}")
            except httpx.HTTPError as e:
                logger.error(f"Failed to start STT container: {e}")
