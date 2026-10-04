"""Render a Ruling as a Discord embed payload (dict matching the Discord API embed object)."""

from app.rules.models import Ruling

# Discord limits: description 4096, field value 1024, total 6000, max 25 fields.
_FIELD_LIMIT = 1024
_DESCRIPTION_LIMIT = 4096
_MAX_FIELDS = 10
_COLOR = {"high": 0x2E7D32, "medium": 0xF9A825, "low": 0xC62828}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_discord(ruling: Ruling) -> dict:
    fields = [
        {
            "name": _clip(f"{ref.name} ({ref.category})", 256),
            "value": _clip(f"> {ref.quote}\n[Archives of Nethys]({ref.aon_url})", _FIELD_LIMIT),
            "inline": False,
        }
        for ref in ruling.raw[:_MAX_FIELDS]
    ]
    footer = f"Confidence: {ruling.confidence}" + (" · RAW only" if ruling.raw_only else "")
    return {
        "title": _clip(ruling.query, 256),
        "description": _clip(ruling.interpretation, _DESCRIPTION_LIMIT),
        "color": _COLOR[ruling.confidence],
        "fields": fields,
        "footer": {"text": footer},
    }
