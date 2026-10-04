"""Render a Ruling as Foundry VTT chat-card HTML."""

from html import escape

from app.rules.models import RuleRef, Ruling

_CONFIDENCE_LABEL = {"high": "●●●", "medium": "●●○", "low": "●○○"}


def _link(ref: RuleRef) -> str:
    if ref.foundry_uuid:
        # Foundry enriches this into a clickable compendium link.
        return f"@UUID[{ref.foundry_uuid}]{{{escape(ref.name)}}}"
    return f'<a href="{escape(ref.aon_url)}" target="_blank" rel="noopener">{escape(ref.name)}</a>'


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{escape(p.strip())}</p>" for p in text.split("\n\n") if p.strip())


def render_foundry(ruling: Ruling) -> str:
    raw = "".join(
        f'<blockquote class="arbiter-raw"><header>{_link(ref)}</header>'
        f"<p>{escape(ref.quote)}</p></blockquote>"
        for ref in ruling.raw
    )
    return (
        f'<div class="vtt-arbiter" data-confidence="{ruling.confidence}">'
        f'<section class="arbiter-raw-section"><h3>RAW</h3>{raw}</section>'
        f'<section class="arbiter-ruling"><h3>Tulkinta '
        f'<span class="arbiter-confidence">{_CONFIDENCE_LABEL[ruling.confidence]}</span></h3>'
        f"{_paragraphs(ruling.interpretation)}</section>"
        "</div>"
    )
