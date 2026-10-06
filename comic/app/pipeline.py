import asyncio
import logging
import os
import re

from app.comfy import generate_comfy_prompt, queue_prompt, wait_for_completion
from app.config import settings
from app.gpu import comic_mode
from app.layout import layout_bubbles
from app.llm import FULL_PAGES, TEST_PAGES, generate_beat_sheet, generate_page_detail
from app.sessions import transcript as get_transcript

logger = logging.getLogger(__name__)


async def run_pipeline(session_id: str, test: bool = False):
    """Write and render a comic. A test run is 1-2 pages and named apart from full comics."""
    transcript = get_transcript(session_id)
    if not transcript.strip():
        logger.warning(f"No transcript found for session {session_id}")
        return

    logger.info(f"Loaded transcript for {session_id}, length: {len(transcript)}")

    # The Gemini calls are blocking; run them in a thread so the worker stays responsive.
    loop = asyncio.get_running_loop()

    page_range = TEST_PAGES if test else FULL_PAGES
    beat_sheet = await loop.run_in_executor(None, generate_beat_sheet, transcript, page_range)
    logger.info(f"Generated beat sheet with {len(beat_sheet.pages)} pages")
    # Gemini doesn't always keep to the count; never render more than asked for.
    outlines = beat_sheet.pages[: page_range[1]]

    pages = []
    for p in outlines:
        page_detail = await loop.run_in_executor(None, generate_page_detail, p, transcript)
        pages.append(page_detail)

    # Only the rendering needs the GPU: transcription is unavailable from here until it ends.
    async with comic_mode(session_id):
        for page in pages:
            for panel in page.panels:
                prefix = f"comic_{session_id}{'_test' if test else ''}_p{page.page_number}"
                await render_panel(f"{prefix}_pan{panel.panel_number}", panel)

    logger.info("Pipeline complete.")


async def render_panel(prefix: str, panel):
    logger.info(f"Rendering {prefix}")
    prompt = generate_comfy_prompt(panel.image_prompt, prefix)
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
