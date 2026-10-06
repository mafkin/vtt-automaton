import logging

from arq.connections import RedisSettings

from app.config import settings
from app.gpu import recover
from app.pipeline import run_pipeline
from app.sessions import any_live, get_session

log = logging.getLogger(__name__)


async def generate_comic(ctx, session_id: str) -> None:
    session = get_session(session_id)
    if session is None or session.live:
        # Checked again here: the session may have been restarted since the job was queued.
        log.error(
            "Not generating a comic for %s: session is unknown or still recording", session_id
        )
        return
    if any_live():
        # Another session is recording: stopping STT would cut it off.
        log.error("Not generating a comic for %s: a session is recording", session_id)
        return
    log.info("Starting comic generation for session %s", session_id)
    try:
        await run_pipeline(session_id)
        log.info("Finished comic generation for session %s", session_id)
    except Exception:
        log.exception("Comic generation failed for session %s", session_id)


class WorkerSettings:
    functions = [generate_comic]
    # Undo a comic cut short by a crash or restart, and stop the ComfyUI that compose up started.
    on_startup = recover
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # arq's defaults (5 min timeout, 10 concurrent jobs, 5 tries) don't fit: a comic takes over
    # an hour, and two jobs at once would fight over the GPU handover.
    job_timeout = settings.job_timeout_seconds
    max_jobs = 1
    max_tries = 1
    # Don't keep results: a kept result would block re-running the same session (same job id).
    keep_result = 0
