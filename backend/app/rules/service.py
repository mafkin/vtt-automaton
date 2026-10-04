"""Rules arbiter pipeline: analyse query -> retrieve RAW -> draft ruling -> validate quotes."""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.llm.base import LLMProvider
from app.rules.models import RuleEntry, RuleRef, Ruling, RulingRequest
from app.rules.store import RulesStore

log = logging.getLogger(__name__)

_WS_RE = re.compile(r"\s+")
_FALLBACK_QUOTE_CHARS = 700
_FALLBACK_ENTRIES = 3

_RAW_ONLY_MESSAGE = {
    "Finnish": "Tulkintaa ei voitu muodostaa luotettavasti. Alla on hakua vastaavat säännöt (RAW).",
}
_RAW_ONLY_MESSAGE_DEFAULT = (
    "A reliable interpretation could not be produced. The matching rules text (RAW) is below."
)


class NoRulesFound(Exception):
    pass


class QueryAnalysis(BaseModel):
    search_terms: list[str] = Field(
        description="Official English Pathfinder 2e Remaster names of the rules elements involved"
    )
    question_en: str = Field(description="The question translated to English")


class Citation(BaseModel):
    entry_id: str
    quote: str = Field(description="Exact, verbatim passage copied from that entry's text")


class RulingDraft(BaseModel):
    citations: list[Citation]
    interpretation: str
    confidence: Literal["high", "medium", "low"]


ANALYSIS_SYSTEM = """\
You map player questions about Pathfinder Second Edition to rules lookups. The table plays with
the Remaster rules only; legacy (pre-Remaster) rules are disregarded.
Players write mostly in Finnish and use English game terms (Strike, off-guard, Trip, etc.).
Return the official English names of every action, condition, spell, feat, trait, creature
ability or rule the question involves. Always use the Remaster name when one exists, even if the
player used a legacy one (off-guard not flat-footed, Reactive Strike not Attack of Opportunity,
Force Barrage not Magic Missile). Also return the question translated to English.
Do not answer the question."""

RULING_SYSTEM = """\
You are a Pathfinder Second Edition rules arbiter for a gaming table that plays with the
Remaster rules only. Legacy (pre-Remaster) rules are disregarded: never apply them, and if the
player used a legacy term, rule with its Remaster replacement.
You are given rules entries retrieved from Archives of Nethys. They are your ONLY source of rules.

The answer is shown in two parts, in this order: first the rules as written (RAW) for every
entry you cite, then your ruling. The ruling must therefore build on the cited RAW.

Output contract:
1. citations: every entry the ruling depends on, most important first. For each, give its
   entry_id and quote the deciding passage character for character from that entry's text.
   Never paraphrase a quote. Do not cite entries that do not matter for the question.
2. interpretation: the ruling for the situation, written in {language}. Apply the cited RAW to
   the situation step by step and finish with a clear verdict. Keep official English game terms
   (action, condition and trait names) in English. If the RAW does not settle the question, say
   so plainly and leave the call to the GM; do not invent rules.
3. confidence: high if the RAW settles it directly, medium if it needs interpretation,
   low if the RAW barely covers it."""


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit]


def _format_entries(entries: list[RuleEntry]) -> str:
    blocks = []
    for e in entries:
        traits = f" [{', '.join(e.traits)}]" if e.traits else ""
        blocks.append(
            f'<entry id="{e.id}" category="{e.category}" name="{e.name}"{traits}>\n'
            f"{e.text}\n</entry>"
        )
    return "\n\n".join(blocks)


def _ref(entry: RuleEntry, quote: str) -> RuleRef:
    return RuleRef(
        entry_id=entry.id,
        category=entry.category,
        name=entry.name,
        aon_url=entry.aon_url,
        source=entry.source,
        text=entry.text,
        quote=quote,
    )


class RulesService:
    def __init__(
        self,
        store: RulesStore,
        llm: LLMProvider,
        answer_language: str = "Finnish",
        max_entries: int = 8,
    ) -> None:
        self._store = store
        self._llm = llm
        self._language = answer_language
        self._max_entries = max_entries

    async def rule(self, request: RulingRequest) -> Ruling:
        analysis = await self._analyse(request.query)
        entries = self._store.search(analysis.search_terms, limit=self._max_entries)
        if not entries:
            raise NoRulesFound(request.query)

        try:
            draft = await self._llm.generate_json(
                system=RULING_SYSTEM.format(language=self._language),
                prompt=self._ruling_prompt(request, analysis, entries),
                schema=RulingDraft,
            )
        except Exception:
            log.exception("Ruling generation failed; returning RAW only")
            return self._raw_only(request.query, entries)

        refs = self.validate_citations(draft.citations, entries)
        if not refs:
            log.warning("No valid citations in ruling draft; returning RAW only")
            return self._raw_only(request.query, entries)

        return Ruling(
            query=request.query,
            raw=refs,
            interpretation=draft.interpretation.strip(),
            confidence=draft.confidence,
        )

    async def _analyse(self, query: str) -> QueryAnalysis:
        try:
            analysis = await self._llm.generate_json(
                system=ANALYSIS_SYSTEM, prompt=query, schema=QueryAnalysis
            )
            if analysis.search_terms:
                return analysis
        except Exception:
            log.exception("Query analysis failed; falling back to raw query terms")
        # Fallback: search with the words of the query itself (works for English terms).
        return QueryAnalysis(search_terms=query.split(), question_en=query)

    def _ruling_prompt(
        self, request: RulingRequest, analysis: QueryAnalysis, entries: list[RuleEntry]
    ) -> str:
        parts = [
            f"Question (original): {request.query}",
            f"Question (English): {analysis.question_en}",
        ]
        if request.context:
            parts.append(f"Game state: {request.context.model_dump_json(exclude_none=True)}")
        parts.append("Rules entries:\n" + _format_entries(entries))
        return "\n\n".join(parts)

    @staticmethod
    def validate_citations(citations: list[Citation], entries: list[RuleEntry]) -> list[RuleRef]:
        """Keep citations whose quote appears verbatim (modulo whitespace) in a retrieved entry."""
        by_id = {e.id: e for e in entries}
        refs: list[RuleRef] = []
        for c in citations:
            entry = by_id.get(c.entry_id)
            quote = _normalize(c.quote)
            if entry is None or not quote:
                log.warning("Dropping citation for unknown entry %r", c.entry_id)
                continue
            if quote not in _normalize(entry.text):
                log.warning("Dropping non-verbatim quote from %r", c.entry_id)
                continue
            refs.append(_ref(entry, quote))
        return refs

    def _raw_only(self, query: str, entries: list[RuleEntry]) -> Ruling:
        refs = [
            _ref(e, _truncate(_normalize(e.text), _FALLBACK_QUOTE_CHARS))
            for e in entries[:_FALLBACK_ENTRIES]
        ]
        return Ruling(
            query=query,
            raw=refs,
            interpretation=_RAW_ONLY_MESSAGE.get(self._language, _RAW_ONLY_MESSAGE_DEFAULT),
            confidence="low",
            raw_only=True,
        )
