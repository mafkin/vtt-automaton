import io

import pytest
from PIL import Image

from app import bible as bible_store
from app import comics, pipeline
from app.comics import Balloon, Limits, Moment, ScriptPage, ScriptPanel
from app.llm import EventsResult


def png(colour="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(buf, "PNG")
    return buf.getvalue()


def a_page(title="Sivu", text="Ota tuo elävänä!") -> ScriptPage:
    return ScriptPage(
        title=title,
        characters=["Pentik"],
        panels=[ScriptPanel(visual="v", balloons=[Balloon(speaker="Pentik", text=text)])],
    )


@pytest.fixture
def gemini(monkeypatch, sessions_db):
    """Fake Gemini. `read` decides what the lettering check sees for each drawing, in order."""
    calls = {"draw": [], "read": [], "script_moments": None}
    reads: list[list[str]] = []

    def extract(transcript, bible):
        calls["transcript"] = transcript
        moments = [Moment(title="Kuulustelu"), Moment(title="Tikari")]
        return EventsResult(events="1. Fight", moments=moments), 1000

    def script(events, moments, bible):
        calls["script_moments"] = [m.title for m in moments]
        return [a_page(f"Sivu {i}") for i, _ in enumerate(moments, 1)], 2000

    def draw(page, bible, cast, extra=""):
        calls["draw"].append((page.title, [c.name for c, _ in cast], extra))
        return png(), 1800

    def read(image):
        calls["read"].append(image)
        return (reads.pop(0) if reads else ["Ota tuo elävänä!"]), 1300

    monkeypatch.setattr(pipeline, "extract_events", extract)
    monkeypatch.setattr(pipeline, "write_script", script)
    monkeypatch.setattr(pipeline, "draw_page", draw)
    monkeypatch.setattr(pipeline, "read_lettering", read)
    ch = bible_store.add_character("Pentik")
    bible_store.add_image(ch.id, png("blue"))
    calls["reads"] = reads
    return calls


async def test_extract_stores_events_moments_and_tokens(gemini):
    c = comics.create("ended1", "Session 12")
    await pipeline.extract(c.id)
    c = comics.load(c.id)
    assert c.status == "events" and c.events == "1. Fight"
    assert [m.title for m in c.moments] == ["Kuulustelu", "Tikari"]
    assert (c.text_tokens, c.image_tokens) == (1000, 0)
    assert "Örkit hyökkäävät." in gemini["transcript"]


async def test_script_uses_the_chosen_moments_and_your_own(gemini):
    c = comics.create("ended1", "S")
    await pipeline.extract(c.id)
    await pipeline.script(c.id, chosen=[1], own="Käl heittää tikarin kattoon")
    c = comics.load(c.id)
    assert gemini["script_moments"] == ["Tikari", "Käl heittää tikarin kattoon"]
    assert c.status == "script" and [p.title for p in c.script] == ["Sivu 1", "Sivu 2"]
    assert (c.text_tokens, c.image_tokens) == (3000, 0)


async def test_draw_letters_checks_and_sends_only_the_pages_cast(gemini):
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    assert c.status == "done" and c.pages[0].versions == ["page_1_v1.png"]
    assert c.pages[0].check == "ok"
    assert gemini["draw"] == [("Sivu", ["Pentik"], "")]
    # The lettering check belongs to drawing: both count against the budget.
    assert (c.text_tokens, c.image_tokens) == (0, 1800 + 1300)


async def test_a_lettering_mismatch_is_redrawn_once_and_reported(gemini):
    gemini["reads"].extend([["Ota tuo elävänä!", "Ota tuo elävänä!"], ["väärin"]])
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    assert len(gemini["draw"]) == 2  # 1 + max_auto_redraws_per_page, never more
    assert c.pages[0].versions == ["page_1_v1.png", "page_1_v2.png"]
    assert c.pages[0].check.startswith("missing: Ota tuo elävänä!")
    assert c.status == "done"


async def test_redraw_one_page_with_an_instruction(gemini):
    c = comics.create("ended1", "S")
    c.script = [a_page("A"), a_page("B")]
    comics.save(c)
    await pipeline.draw(c.id, page_index=1, extra="Pentik holds his shield")
    assert gemini["draw"] == [("B", ["Pentik"], "Pentik holds his shield")]
    assert comics.load(c.id).pages[1].versions == ["page_2_v1.png"]


async def test_the_budget_stops_before_an_image_call(gemini):
    comics.save_limits(Limits(token_budget_per_comic=3000))
    c = comics.create("ended1", "S")
    c.script = [a_page("A"), a_page("B")]
    c.image_tokens = 1000
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    assert gemini["draw"] == []  # 1000 + one page's estimate > 3000: nothing was drawn
    assert c.status == "budget" and "1000 / 3000" in c.message


async def test_a_failure_is_shown_and_never_leaves_the_comic_busy(gemini, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Gemini returned no image")

    monkeypatch.setattr(pipeline, "draw_page", broken)
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    assert c.status == "failed" and "no image" in c.message


async def test_reading_a_long_transcript_never_hits_the_budget(gemini, monkeypatch):
    comics.save_limits(Limits(token_budget_per_comic=100))

    def expensive(transcript, bible):
        return EventsResult(events="1. Fight", moments=[]), 34_000

    monkeypatch.setattr(pipeline, "extract_events", expensive)
    c = comics.create("ended1", "S")
    await pipeline.extract(c.id)
    c = comics.load(c.id)
    assert c.status == "events" and c.text_tokens == 34_000
