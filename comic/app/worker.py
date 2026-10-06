import logging
import asyncio
from arq.connections import RedisSettings
from app.config import settings
from app.gpu import gpu_lease
# import LLM routines and comfyUI routines
from app.pipeline import run_pipeline

logger = logging.getLogger(__name__)

async def generate_comic(ctx, session_id: str):
    logger.info(f"Starting comic generation for session {session_id}")
    try:
        await run_pipeline(session_id)
        logger.info(f"Finished comic generation for session {session_id}")
    except Exception as e:
        logger.error(f"Comic generation failed: {e}", exc_info=True)

class WorkerSettings:
    functions = [generate_comic]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
