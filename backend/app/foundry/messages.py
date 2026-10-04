"""Builds the ruling messages sent to Foundry (for chat- and voice-asked questions alike)."""

import logging
from typing import Any

from app.render.foundry import render_foundry
from app.rules.models import RulingRequest
from app.rules.service import NoRulesFound, RulesService

log = logging.getLogger(__name__)


async def run_ruling(
    service: RulesService, language: str, request: RulingRequest, message_id: str
) -> dict[str, Any]:
    """Run a ruling and build the ``ruling.result`` or ``ruling.error`` message."""
    try:
        ruling = await service.rule(request)
    except NoRulesFound:
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
    return {
        "type": "ruling.result",
        "id": message_id,
        "ruling": ruling.model_dump(mode="json"),
        "html": render_foundry(ruling, language),
    }
