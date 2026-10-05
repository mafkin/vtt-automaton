"""Builds the ruling messages sent to Foundry (for chat- and voice-asked questions alike)."""

import logging
import time
from typing import Any

from app.render.foundry import render_foundry
from app.rules.models import RulingRequest
from app.rules.service import NoRulesFound, RulesService

log = logging.getLogger(__name__)


async def run_ruling(
    service: RulesService, language: str, request: RulingRequest, message_id: str
) -> dict[str, Any]:
    """Run a ruling and build the ``ruling.result`` or ``ruling.error`` message."""
    started = time.monotonic()
    try:
        ruling = await service.rule(request)
    except NoRulesFound:
        log.info("Ruling %s: no matching rules (%.1f s)", message_id, time.monotonic() - started)
        return {
            "type": "ruling.error",
            "id": message_id,
            "code": "rules.no_match",
            "message": "No matching rules entries found",
        }
    except Exception:
        log.exception("Ruling failed")
        return {
            "type": "ruling.error",
            "id": message_id,
            "code": "internal",
            "message": "Ruling failed",
        }
    log.info(
        "Ruling %s took %.1f s: %d rules cited, confidence %s%s",
        message_id,
        time.monotonic() - started,
        len(ruling.raw),
        ruling.confidence,
        ", RAW only" if ruling.raw_only else "",
    )
    return {
        "type": "ruling.result",
        "id": message_id,
        "ruling": ruling.model_dump(mode="json"),
        "html": render_foundry(ruling, language),
    }
