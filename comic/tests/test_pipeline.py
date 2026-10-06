from contextlib import asynccontextmanager

import pytest

from app import pipeline
from app.llm import (
    FULL_PAGES,
    TEST_PAGES,
    BeatSheet,
    PageDetail,
    PageOutline,
    Panel,
    beat_sheet_prompt,
)


def test_production_comics_stay_8_to_10_pages():
    assert FULL_PAGES == (8, 10)
    assert TEST_PAGES == (1, 2)
    assert "comic book issue of 8-10 pages" in beat_sheet_prompt("GM: Hei.", FULL_PAGES)
    assert "comic book issue of 1-2 pages" in beat_sheet_prompt("GM: Hei.", TEST_PAGES)


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Runs run_pipeline without Gemini, ComfyUI or Docker. Returns what it was asked to do."""
    seen = {"pages": None, "rendered": []}

    def beat_sheet(transcript, pages):
        seen["pages"] = pages
        # Gemini doesn't always keep to the count: five pages for a 1-2 page request.
        return BeatSheet(pages=[PageOutline(page_number=n, beats=[]) for n in range(1, 6)])

    def page_detail(outline, transcript):
        panel = Panel(panel_number=1, image_prompt="x", character_focus=[], speech_bubbles=[])
        return PageDetail(page_number=outline.page_number, panels=[panel])

    @asynccontextmanager
    async def no_gpu(session_id):
        yield

    async def render(prefix, panel):
        seen["rendered"].append(prefix)

    monkeypatch.setattr(pipeline, "get_transcript", lambda session_id: "GM: Hei.")
    monkeypatch.setattr(pipeline, "generate_beat_sheet", beat_sheet)
    monkeypatch.setattr(pipeline, "generate_page_detail", page_detail)
    monkeypatch.setattr(pipeline, "comic_mode", no_gpu)
    monkeypatch.setattr(pipeline, "render_panel", render)
    return seen


async def test_a_full_run_asks_for_8_to_10_pages(fake_pipeline):
    await pipeline.run_pipeline("s1")
    assert fake_pipeline["pages"] == FULL_PAGES
    assert fake_pipeline["rendered"][0] == "comic_s1_p1_pan1"


async def test_a_test_run_is_capped_at_2_pages_and_named_apart(fake_pipeline):
    await pipeline.run_pipeline("s1", test=True)
    assert fake_pipeline["pages"] == TEST_PAGES
    assert fake_pipeline["rendered"] == ["comic_s1_test_p1_pan1", "comic_s1_test_p2_pan1"]
