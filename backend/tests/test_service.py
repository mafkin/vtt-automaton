import pytest

from app.llm.fake import FakeProvider
from app.rules.models import RulingRequest
from app.rules.service import (
    Citation,
    NoRulesFound,
    QueryAnalysis,
    RulesService,
    RulingDraft,
)


def scripted(analysis: QueryAnalysis, draft: RulingDraft | Exception):
    def respond(system, prompt, schema):
        if schema is QueryAnalysis:
            return analysis
        if isinstance(draft, Exception):
            raise draft
        return draft

    return FakeProvider(respond)


ANALYSIS = QueryAnalysis(search_terms=["Trip", "Prone"], question_en="What happens on a Trip?")


async def test_valid_citations_are_kept(store):
    draft = RulingDraft(
        citations=[
            Citation(entry_id="Actions.aspx?ID=1", quote="the target falls and   lands prone."),
            Citation(entry_id="Conditions.aspx?ID=2", quote="You are off-guard"),
        ],
        interpretation="Kohde kaatuu ja on off-guard.",
        confidence="high",
    )
    service = RulesService(store, scripted(ANALYSIS, draft))

    ruling = await service.rule(RulingRequest(query="Mitä Trip tekee?"))

    assert not ruling.raw_only
    assert [r.name for r in ruling.raw] == ["Trip", "Prone"]
    assert ruling.raw[0].quote == "the target falls and lands prone."
    assert ruling.confidence == "high"


async def test_fabricated_quotes_are_dropped(store):
    draft = RulingDraft(
        citations=[
            Citation(entry_id="Actions.aspx?ID=1", quote="the target is grabbed"),
            Citation(entry_id="Conditions.aspx?ID=3", quote="You take a circumstance penalty"),
            Citation(entry_id="not-retrieved", quote="anything"),
        ],
        interpretation="...",
        confidence="medium",
    )
    analysis = QueryAnalysis(search_terms=["Trip", "Off-Guard"], question_en="")
    service = RulesService(store, scripted(analysis, draft))

    ruling = await service.rule(RulingRequest(query="q"))

    assert not ruling.raw_only
    assert [r.name for r in ruling.raw] == ["Off-Guard"]


async def test_all_invalid_citations_fall_back_to_raw_only(store):
    draft = RulingDraft(
        citations=[Citation(entry_id="Actions.aspx?ID=1", quote="invented text")],
        interpretation="Väärä vastaus",
        confidence="high",
    )
    service = RulesService(store, scripted(ANALYSIS, draft))

    ruling = await service.rule(RulingRequest(query="q"))

    assert ruling.raw_only
    assert ruling.confidence == "low"
    assert ruling.raw[0].name == "Trip"
    assert "Väärä" not in ruling.interpretation


async def test_llm_error_falls_back_to_raw_only(store):
    service = RulesService(store, scripted(ANALYSIS, RuntimeError("quota")))

    ruling = await service.rule(RulingRequest(query="q"))

    assert ruling.raw_only


async def test_analysis_failure_uses_query_words(store):
    def respond(system, prompt, schema):
        if schema is QueryAnalysis:
            raise RuntimeError("down")
        return RulingDraft(citations=[], interpretation="", confidence="low")

    service = RulesService(store, FakeProvider(respond))

    ruling = await service.rule(RulingRequest(query="Trip"))

    assert ruling.raw_only
    assert ruling.raw[0].name == "Trip"


async def test_no_matching_rules_raises(store):
    service = RulesService(
        store, scripted(QueryAnalysis(search_terms=["Fireball"], question_en=""), RuntimeError())
    )
    with pytest.raises(NoRulesFound):
        await service.rule(RulingRequest(query="tulipallo"))
