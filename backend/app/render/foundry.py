"""Render a Ruling as Foundry VTT chat-card HTML: rules as written first, then the ruling."""

from html import escape

from app.render.labels import labels_for
from app.rules.models import RuleRef, Ruling

# The full rules text is always printed in Foundry chat. Entries longer than this (e.g. whole
# rules sections) start collapsed so they don't flood the chat log; one click expands them.
_COLLAPSE_OVER_CHARS = 1500


def _link(ref: RuleRef) -> str:
    if ref.foundry_uuid:
        # Foundry enriches this into a clickable compendium link.
        return f"@UUID[{ref.foundry_uuid}]{{{escape(ref.name)}}}"
    return f'<a href="{escape(ref.aon_url)}" target="_blank" rel="noopener">{escape(ref.name)}</a>'


def _paragraphs(text: str) -> str:
    return "".join(
        "<p>" + "<br>".join(escape(line) for line in p.strip().split("\n")) + "</p>"
        for p in text.split("\n\n")
        if p.strip()
    )


def _raw_block(ref: RuleRef) -> str:
    source = f' <span class="arbiter-source">{escape(ref.source)}</span>' if ref.source else ""
    header = f"{_link(ref)}{source}"
    body = _paragraphs(ref.text)
    if len(ref.text) > _COLLAPSE_OVER_CHARS:
        content = f"<details><summary>{header}</summary>{body}</details>"
    else:
        content = f"<header>{header}</header>{body}"
    entry = escape(ref.entry_id)
    return f'<blockquote class="arbiter-raw" data-entry="{entry}">{content}</blockquote>'


def render_foundry(ruling: Ruling, language: str = "Finnish") -> str:
    labels = labels_for(language)
    raw = "".join(_raw_block(ref) for ref in ruling.raw)
    confidence = labels["confidence"][ruling.confidence]
    if ruling.raw_only:
        confidence += f" · {labels['raw_only']}"
    return (
        f'<div class="vtt-arbiter" data-confidence="{ruling.confidence}">'
        f'<section class="arbiter-raw-section"><h3>{labels["raw"]}</h3>{raw}</section>'
        f'<section class="arbiter-ruling"><h3>{labels["ruling"]} '
        f'<span class="arbiter-confidence">({confidence})</span></h3>'
        f"{_paragraphs(ruling.interpretation)}</section>"
        "</div>"
    )
