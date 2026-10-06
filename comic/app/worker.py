import logging

from arq.connections import RedisSettings

from app import pipeline
from app.config import settings

log = logging.getLogger(__name__)


async def extract_job(ctx, comic_id: str) -> None:
    await pipeline.extract(comic_id)


async def script_job(ctx, comic_id: str, chosen: list[int], own: str = "") -> None:
    await pipeline.script(comic_id, chosen, own)


async def draw_job(ctx, comic_id: str, page_index: int | None = None, extra: str = "") -> None:
    await pipeline.draw(comic_id, page_index, extra)


class WorkerSettings:
    functions = [extract_job, script_job, draw_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    job_timeout = settings.job_timeout_seconds
    # One step at a time: comic.json has one writer, and the budget check sees every call.
    max_jobs = 1
    max_tries = 1
    # Don't keep results: a kept result would block queueing the same step again (same job id).
    keep_result = 0
