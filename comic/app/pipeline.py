import asyncio
import json
import logging
import os
import re
import secrets

from app import bible as bible_store
from app.bible import Bible
from app.comfy import generate_comfy_prompt, queue_prompt, wait_for_completion
from app.config import settings
from app.gpu import comic_mode
from app.layout import layout_bubbles
from app.llm import (
    FULL_PAGES,
    TEST_PAGES,
    bible_context,
    generate_beat_sheet,
    generate_page_detail,
)
from app.sessions import transcript as get_transcript

logger = logging.getLogger(__name__)


async def run_pipeline(session_id: str, test: bool = False):
    """Write and render a comic. A test run is 1-2 pages and named apart from full comics."""
    transcript = get_transcript(session_id)
    if not transcript.strip():
        logger.warning(f"No transcript found for session {session_id}")
        return

    logger.info(f"Loaded transcript for {session_id}, length: {len(transcript)}")

    name = f"comic_{session_id}{'_test' if test else ''}"
    # The bible as it is now: edits on the dashboard during a run don't change this comic.
    bible = bible_store.load()
    seed = secrets.randbelow(2**31)
    _save_snapshot(name, session_id, test, bible, seed)
    context = bible_context(bible)

    # The Gemini calls are blocking; run them in a thread so the worker stays responsive.
    loop = asyncio.get_running_loop()

    page_range = TEST_PAGES if test else FULL_PAGES
    beat_sheet = await loop.run_in_executor(
        None, generate_beat_sheet, transcript, page_range, context
    )
    logger.info(f"Generated beat sheet with {len(beat_sheet.pages)} pages")
    # Gemini doesn't always keep to the count; never render more than asked for.
    outlines = beat_sheet.pages[: page_range[1]]

    pages = []
    for p in outlines:
        page_detail = await loop.run_in_executor(
            None, generate_page_detail, p, transcript, context, bible.bubble_language
        )
        pages.append(page_detail)

    # Only the rendering needs the GPU: transcription is unavailable from here until it ends.
    async with comic_mode(session_id):
        for page in pages:
            for panel in page.panels:
                positive, negative, reference = panel_prompt(bible, panel)
                await render_panel(
                    f"{name}_p{page.page_number}_pan{panel.panel_number}",
                    panel,
                    positive=positive,
                    negative=negative,
                    # One seed per comic; each panel its own variation of it.
                    seed=seed + page.page_number * 100 + panel.panel_number,
                    reference=reference,
                )

    logger.info("Pipeline complete.")


def panel_prompt(bible: Bible, panel) -> tuple[str, str, str | None]:
    """Positive prompt, negative prompt and IP-Adapter reference image for one panel.

    The reference is used only for a panel about exactly one character with images: a single
    IP-Adapter over two characters blends them into one.
    """
    characters = bible.match(panel.character_focus)
    looks = [f"{c.name}: {c.appearance}" for c in characters if c.appearance]
    positive = ", ".join(p for p in (bible.style.positive, panel.image_prompt, *looks) if p)
    reference = None
    if len(panel.character_focus) == 1 and len(characters) == 1 and characters[0].images:
        reference = f"characters/{characters[0].id}/{characters[0].images[0]}"
    return positive, bible.style.negative, reference


def _save_snapshot(name: str, session_id: str, test: bool, bible: Bible, seed: int) -> None:
    """What this comic was made with, next to its panels (reproducible; read by later steps)."""
    path = os.path.join(settings.comfy_output_dir, f"{name}_bible.json")
    snapshot = {"session_id": session_id, "test": test, "seed": seed, "bible": bible.model_dump()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


async def render_panel(
    prefix: str,
    panel,
    positive: str,
    negative: str,
    seed: int,
    reference: str | None = None,
):
    logger.info(f"Rendering {prefix}" + (f" with reference {reference}" if reference else ""))
    prompt = generate_comfy_prompt(positive, prefix, negative, seed, reference)
    res, client_id = await queue_prompt(prompt)
    prompt_id = res.get("prompt_id")
    if prompt_id:
        success = await wait_for_completion(prompt_id, client_id)
        if not success:
            logger.error(f"Panel {panel.panel_number} failed to render.")
        else:
            img_path = latest_output(prefix)
            if img_path is None:
                logger.error(f"Rendered {prefix} but found no output file")
            else:
                layout_bubbles(img_path, panel.speech_bubbles)


def latest_output(prefix: str) -> str | None:
    """The newest render of a prefix: ComfyUI never overwrites, it counts up
    (<prefix>_00001_.png, then _00002_ when the same session is rendered again)."""
    pattern = re.compile(rf"{re.escape(prefix)}_(\d+)_\.png")
    counters = [
        (int(m.group(1)), name)
        for name in os.listdir(settings.comfy_output_dir)
        if (m := pattern.fullmatch(name))
    ]
    return os.path.join(settings.comfy_output_dir, max(counters)[1]) if counters else None
