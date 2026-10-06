import io

import pytest
from PIL import Image

from app import bible as bible_store
from app import comics, pipeline
from app.comics import Balloon, Limits, Moment, ScriptPage, ScriptPanel
from app.llm import EventsResult, Inspection, LookProblem


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
    """Fake Gemini. `reads`/`looks` decide what the inspection sees for each drawing, in order."""
    calls = {"draw": [], "read": [], "script_moments": None, "previous": [], "look_check": []}
    reads: list[list[str]] = []
    looks: list[list[LookProblem]] = []

    def extract(transcript, bible):
        calls["transcript"] = transcript
        moments = [Moment(title="Kuulustelu"), Moment(title="Tikari")]
        return EventsResult(events="1. Fight", moments=moments), 1000

    def script(events, moments, bible):
        calls["script_moments"] = [m.title for m in moments]
        return [a_page(f"Sivu {i}") for i, _ in enumerate(moments, 1)], 2000

    def draw(page, bible, cast, extra="", anchor=None, previous=None):
        calls["draw"].append((page.title, [c.name for c, _ in cast], extra))
        calls["anchor"] = anchor
        calls["previous"].append(previous)
        calls["cast_png"] = [img for _, refs in cast for _, img in refs]
        calls["cast_labels"] = [[label for label, _ in refs] for _, refs in cast]
        calls["drawn"] = len(calls["draw"])
        return png(f"#{len(calls['draw']):06x}"), 1800

    def inspect(image, cast, look_check=True):
        calls["read"].append(image)
        calls["look_check"].append(look_check)
        texts = reads.pop(0) if reads else ["Ota tuo elävänä!"]
        return Inspection(texts=texts, looks=looks.pop(0) if looks else []), 1300

    monkeypatch.setattr(pipeline, "extract_events", extract)
    monkeypatch.setattr(pipeline, "write_script", script)
    monkeypatch.setattr(pipeline, "draw_page", draw)
    monkeypatch.setattr(pipeline, "inspect_page", inspect)
    ch = bible_store.add_character("Pentik")
    bible_store.add_image(ch.id, png("blue"))
    calls["reads"] = reads
    calls["looks"] = looks
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


async def test_draw_uses_the_approved_sheet_and_the_style_anchor(gemini):
    sheet = png("green")
    bible_store.approve_sheet("pentik", bible_store.add_sheet("pentik", sheet))
    bible_store.set_anchor(png("white"))
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    assert gemini["cast_png"] == [sheet] and gemini["cast_labels"] == [["full-body sheet"]]
    assert gemini["anchor"] == bible_store.anchor_image()
    c = comics.load(c.id)
    info = c.pages[0].info["page_1_v1.png"]
    assert info.refs == {"Pentik": "sheet_1.png"} and info.anchor is True
    assert info.check == "ok" and info.tokens == 1800 + 1300 and info.round


async def test_each_draw_is_its_own_round(gemini):
    c = comics.create("ended1", "S")
    c.script = [a_page("A"), a_page("B")]
    comics.save(c)
    await pipeline.draw(c.id)
    await pipeline.draw(c.id, page_index=1, extra="kilpi")
    rounds = comics.rounds(comics.load(c.id))
    assert len(rounds) == 2 and rounds[1].pages == [None, "page_2_v2.png"]
    assert rounds[0].label == "Pentik: image"


async def test_each_page_sees_the_previous_page_of_the_round(gemini):
    c = comics.create("ended1", "S")
    c.script = [a_page("A"), a_page("B"), a_page("C")]
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    first, second = c.pages[0].current, c.pages[1].current
    path = lambda name: comics.page_path(c.id, name).read_bytes()  # noqa: E731
    assert gemini["previous"] == [None, path(first), path(second)]
    assert c.pages[1].info[second].previous == first and c.pages[0].info[first].previous is None


async def test_a_single_redraw_sees_the_current_page_before_it(gemini):
    c = comics.create("ended1", "S")
    c.script = [a_page("A"), a_page("B")]
    comics.save(c)
    await pipeline.draw(c.id)
    gemini["previous"].clear()
    await pipeline.draw(c.id, page_index=1)
    c = comics.load(c.id)
    assert gemini["previous"] == [comics.page_path(c.id, "page_1_v1.png").read_bytes()]


async def test_the_detail_sheet_is_sent_after_the_full_body_sheet(gemini):
    bible_store.approve_sheet("pentik", bible_store.add_sheet("pentik", png("green")))
    bible_store.approve_sheet("pentik", bible_store.add_sheet("pentik", png("blue"), kind="detail"))
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    assert gemini["cast_labels"] == [["full-body sheet", "close-ups of details"]]
    info = comics.load(c.id).pages[0].info["page_1_v1.png"]
    assert info.refs == {"Pentik": "sheet_1.png + detail_2.png"}


async def test_a_look_problem_is_redrawn_once_and_shown(gemini):
    shield = LookProblem(character="Pentik", problem="red castle on a white tabard")
    gemini["looks"].extend([[shield], [shield]])
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    c = comics.load(c.id)
    assert len(gemini["draw"]) == 2
    assert c.pages[0].check == "ok"  # the lettering was fine
    assert c.pages[0].looks == "Pentik: red castle on a white tabard"
    assert c.pages[0].info["page_1_v2.png"].looks == c.pages[0].looks


async def test_the_look_check_can_be_switched_off(gemini):
    comics.save_limits(Limits(look_check=False))
    gemini["looks"].append([LookProblem(character="Pentik", problem="ignored")])
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id)
    assert gemini["look_check"] == [False] and len(gemini["draw"]) == 1
    assert comics.load(c.id).pages[0].looks == ""


async def test_the_automatic_redraw_is_told_what_to_fix(gemini):
    # Re-rolling with the same prompt brought the same mistakes back.
    gemini["looks"].append([LookProblem(character="Pentik", problem="shield has no gold castle")])
    gemini["reads"].append(["Ota tuo elävänä!", "Kirjaston ja Restov."])
    c = comics.create("ended1", "S")
    c.script = [a_page()]
    comics.save(c)
    await pipeline.draw(c.id, extra="Pentik on the left")
    first, second = gemini["draw"][0][2], gemini["draw"][1][2]
    assert first == "Pentik on the left"
    assert second.startswith("Pentik on the left")
    assert "Fix these mistakes of the previous attempt" in second
    assert "Pentik: shield has no gold castle" in second
    assert "extra: Kirjaston ja Restov." in second
