import asyncio
import logging
from pathlib import Path

from app.ingest.aon import import_rules

log = logging.getLogger(__name__)


async def refresh_rules_periodically(db_path: Path, interval_hours: float) -> None:
    """Check AoN for a new build on startup and then every ``interval_hours``.

    Each check costs one small query; a full import only runs when AoN has rebuilt its index.
    """
    while True:
        try:
            await asyncio.to_thread(import_rules, db_path)
        except Exception:
            log.exception("Rules refresh failed; keeping the current DB")
        await asyncio.sleep(interval_hours * 3600)
