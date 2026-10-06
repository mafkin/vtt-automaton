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


async def test_a_rerun_letters_the_new_panel_not_the_old_one(tmp_path, monkeypatch):
    # ComfyUI never overwrites: a second render of the same prefix is saved as _00002_.
    from PIL import Image

    from app.config import settings

    monkeypatch.setattr(settings, "comfy_output_dir", str(tmp_path))
    for counter, colour in (("00001", "red"), ("00002", "blue")):
        Image.new("RGB", (64, 64), colour).save(tmp_path / f"comic_s1_p1_pan1_{counter}_.png")
    Image.new("RGB", (64, 64), "red").save(tmp_path / "comic_s1_p1_pan1_00001__lettered.png")
    # A longer prefix that starts the same must not be picked up.
    Image.new("RGB", (64, 64), "red").save(tmp_path / "comic_s1_p1_pan10_00009_.png")

    async def queued(prompt):
        return {"prompt_id": "p"}, "c"

    async def done(prompt_id, client_id):
        return True

    lettered = []
    monkeypatch.setattr(pipeline, "queue_prompt", queued)
    monkeypatch.setattr(pipeline, "wait_for_completion", done)
    monkeypatch.setattr(pipeline, "layout_bubbles", lambda path, bubbles: lettered.append(path))

    panel = Panel(panel_number=1, image_prompt="x", character_focus=[], speech_bubbles=["Hei"])
    await pipeline.render_panel("comic_s1_p1_pan1", panel)
    assert lettered == [str(tmp_path / "comic_s1_p1_pan1_00002_.png")]
