import asyncio
import time

import pytest

from app.llm.fake import FakeProvider
from app.rules.service import Citation, QueryAnalysis, RulesService, RulingDraft
from app.voice.service import TranscriptSegment, VoiceRulesService


class FakeHub:
    def __init__(self, connected=True):
        self.connected = connected
        self.sent = []

    async def broadcast(self, message):
        self.sent.append(message)
        return 1


def make_llm():
    def respond(system, prompt, schema):
        if schema is QueryAnalysis:
            return QueryAnalysis(search_terms=["Trip"], question_en="Trip?")
        return RulingDraft(
            citations=[Citation(entry_id="Actions.aspx?ID=1", quote="lands prone.")],
            interpretation="Kyllä.",
            confidence="high",
        )

    return FakeProvider(respond)


@pytest.fixture
def llm():
    return make_llm()


def make_service(store, llm, hub):
    return VoiceRulesService(
        RulesService(store, llm), hub, language="Finnish", wake_words=["Nethys"]
    )


async def drain():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_spoken_question_is_ruled_and_pushed_with_context(store, llm):
    hub = FakeHub()
    service = make_service(store, llm, hub)

    narration = TranscriptSegment(speaker="GM", text="Örkki juoksee ohi.", t=100)
    assert service.handle_segment(narration) is None
    ruling_id = service.handle_segment(
        TranscriptSegment(speaker="Aino", text="Nethys, voinko kaataa sen Tripillä?", t=110)
    )
    await drain()

    assert ruling_id
    pending, result = hub.sent
    assert pending == {
        "type": "ruling.pending",
        "id": ruling_id,
        "origin": "voice",
        "speaker": "Aino",
        "query": "voinko kaataa sen Tripillä?",
        "mode": "public",
    }
    assert result["type"] == "ruling.result" and result["id"] == ruling_id
    assert result["speaker"] == "Aino" and "Tulkinta" in result["html"]
    ruling_prompt = llm.calls[-1][1]
    assert "GM: Örkki juoksee ohi." in ruling_prompt


async def test_old_talk_is_not_sent_as_context(store, llm):
    service = make_service(store, llm, FakeHub())
    service.handle_segment(TranscriptSegment(speaker="GM", text="Vanha juttu.", t=0))
    question = TranscriptSegment(speaker="Aino", text="Nethys, mitä Trip tekee?", t=500)
    service.handle_segment(question)
    await drain()
    assert "Vanha juttu" not in llm.calls[-1][1]


async def test_repeated_question_is_ignored_within_cooldown(store, llm):
    service = make_service(store, llm, FakeHub())
    first = TranscriptSegment(speaker="Aino", text="Nethys, mitä Trip tekee?", t=0)
    assert service.handle_segment(first)
    again = TranscriptSegment(speaker="Ville", text="Nethys mitä Trip tekee", t=10)
    assert service.handle_segment(again) is None
    later = TranscriptSegment(speaker="Ville", text="Nethys mitä Trip tekee", t=60)
    assert service.handle_segment(later)
    await drain()


async def test_no_ruling_without_foundry_client(store, llm):
    hub = FakeHub(connected=False)
    service = make_service(store, llm, hub)
    seg = TranscriptSegment(speaker="Aino", text="Nethys, mitä Trip tekee?", t=0)
    assert service.handle_segment(seg) is None
    await drain()
    assert hub.sent == [] and llm.calls == []


async def test_ruling_and_end_to_end_times_are_logged(store, llm, caplog):
    service = make_service(store, llm, FakeHub())
    with caplog.at_level("INFO"):
        ruling_id = service.handle_segment(
            TranscriptSegment(speaker="Aino", text="Nethys, mitä Trip tekee?", t=time.time() - 2)
        )
        await drain()
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith(f"Spoken question {ruling_id} from Aino") for m in messages)
    assert any(
        m.startswith(f"Ruling {ruling_id} took") and "confidence high" in m for m in messages
    )
    done = next(m for m in messages if "answered" in m)
    assert float(done.split("answered ")[1].split(" s")[0]) >= 2.0
