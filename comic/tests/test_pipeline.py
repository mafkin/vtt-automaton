import json
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
def fake_pipeline(monkeypatch, tmp_path):
    """Runs run_pipeline without Gemini, ComfyUI or Docker. Returns what it was asked to do."""
    from app.config import settings

    monkeypatch.setattr(settings, "comfy_output_dir", str(tmp_path))
    seen = {"pages": None, "rendered": [], "render_kwargs": []}

    def beat_sheet(transcript, pages, context=""):
        seen["pages"] = pages
        # Gemini doesn't always keep to the count: five pages for a 1-2 page request.
        return BeatSheet(pages=[PageOutline(page_number=n, beats=[]) for n in range(1, 6)])

    def page_detail(outline, transcript, context="", language="Finnish"):
        panel = Panel(panel_number=1, image_prompt="x", character_focus=[], speech_bubbles=[])
        return PageDetail(page_number=outline.page_number, panels=[panel])

    @asynccontextmanager
    async def no_gpu(session_id):
        yield

    async def render(prefix, panel, **kwargs):
        seen["rendered"].append(prefix)
        seen["render_kwargs"].append(kwargs)

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
    await pipeline.render_panel("comic_s1_p1_pan1", panel, positive="x", negative="", seed=1)
    assert lettered == [str(tmp_path / "comic_s1_p1_pan1_00002_.png")]


# --- comic bible in the script and the render ---------------------------------------------

from app import bible as bible_store  # noqa: E402
from app.bible import Bible, Character, Style  # noqa: E402
from app.llm import bible_context, page_detail_prompt  # noqa: E402


def campaign() -> Bible:
    return Bible(
        setting="Restov, a Brevic city with Eastern European architecture",
        tone="light-hearted",
        bubble_language="Finnish",
        style=Style(positive="ink comic", negative="blurry"),
        characters=[
            Character(
                id="rintaro",
                name="Rintaro",
                aliases=["Rin"],
                appearance="Uminari swordsman, red kimono",
                images=["aaaaaaaaaaaaaaaa.png"],
            ),
            Character(id="kal", name="Käl", appearance="small goblin, green cloak"),
        ],
    )


def test_the_script_gets_setting_tone_and_character_sheet():
    context = bible_context(campaign())
    assert "Restov" in context and "light-hearted" in context
    assert "Rintaro (also: Rin): Uminari swordsman, red kimono" in context
    assert "comic book issue of 8-10 pages" in beat_sheet_prompt("GM: Hei.", FULL_PAGES, context)
    assert "Restov" in beat_sheet_prompt("GM: Hei.", FULL_PAGES, context)


def test_the_panel_script_sees_the_transcript_and_the_bubble_language():
    outline = PageOutline(page_number=1, beats=[])
    prompt = page_detail_prompt(outline, "Jessan: Paskanmakuista kalaa!", "CTX", "Finnish")
    assert "Jessan: Paskanmakuista kalaa!" in prompt and "CTX" in prompt
    assert "in Finnish" in prompt


def panel(*focus: str) -> Panel:
    return Panel(
        panel_number=1,
        image_prompt="drinking at a banquet",
        character_focus=list(focus),
        speech_bubbles=[],
    )


def test_panel_prompt_adds_style_and_appearance():
    positive, negative, reference = pipeline.panel_prompt(campaign(), panel("Rin", "Käl"))
    assert positive == (
        "ink comic, drinking at a banquet, Rintaro: Uminari swordsman, red kimono, "
        "Käl: small goblin, green cloak"
    )
    assert negative == "blurry"
    assert reference is None  # two characters: one IP-Adapter would blend them


def test_a_single_character_with_images_gets_an_ip_adapter_reference():
    _, _, reference = pipeline.panel_prompt(campaign(), panel("Rintaro"))
    assert reference == "characters/rintaro/aaaaaaaaaaaaaaaa.png"
    assert pipeline.panel_prompt(campaign(), panel("Käl"))[2] is None  # no images
    assert pipeline.panel_prompt(campaign(), panel("Stranger"))[2] is None


async def test_a_run_uses_and_saves_the_bible_and_one_seed(fake_pipeline, tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "comfy_output_dir", str(tmp_path))
    bible_store.save(campaign())
    await pipeline.run_pipeline("s1", test=True)
    snapshot = json.loads((tmp_path / "comic_s1_test_bible.json").read_text())
    assert snapshot["bible"]["setting"].startswith("Restov") and snapshot["test"] is True
    seeds = [kw["seed"] for kw in fake_pipeline["render_kwargs"]]
    assert len(set(seeds)) == len(seeds)  # each panel its own seed, derived from the comic's
    assert seeds[0] == snapshot["seed"] + 101


def test_the_appearance_draft_leaves_out_pose_and_lighting():
    # The text goes into every panel: "seen from behind" would turn the character around in all.
    from app.llm import describe_prompt

    prompt = describe_prompt("Rintaro", "carries a katana")
    assert "pose" in prompt and "viewpoint" in prompt and "lighting" in prompt
    assert "carries a katana" in prompt and "at most 40 words" in prompt


def test_image_prompts_must_restate_the_campaign_setting():
    # In the first bible test run, a kimono in the appearance text turned Restov Japanese.
    outline = PageOutline(page_number=1, beats=[])
    prompt = page_detail_prompt(outline, "", "Setting: Restov", "Finnish")
    assert "name the location and its architecture as the campaign setting describes it" in prompt
