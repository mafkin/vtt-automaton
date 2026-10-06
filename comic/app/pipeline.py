import asyncio
import logging
import os

from app.comfy import generate_comfy_prompt, queue_prompt, wait_for_completion
from app.config import settings
from app.gpu import gpu_lease
from app.layout import layout_bubbles
from app.llm import generate_beat_sheet, generate_page_detail
from app.sessions import transcript as get_transcript

logger = logging.getLogger(__name__)


async def run_pipeline(session_id: str):
    transcript = get_transcript(session_id)
    if not transcript.strip():
        logger.warning(f"No transcript found for session {session_id}")
        return

    logger.info(f"Loaded transcript for {session_id}, length: {len(transcript)}")

    # The Gemini calls are blocking; run them in a thread so the worker stays responsive.
    loop = asyncio.get_running_loop()

    beat_sheet = await loop.run_in_executor(None, generate_beat_sheet, transcript)
    logger.info(f"Generated beat sheet with {len(beat_sheet.pages)} pages")

    pages = []
    for p in beat_sheet.pages:
        page_detail = await loop.run_in_executor(None, generate_page_detail, p, transcript)
        pages.append(page_detail)

    # GPU execution
    async with gpu_lease():
        for page in pages:
            for panel in page.panels:
                await render_panel(session_id, page.page_number, panel)

    logger.info("Pipeline complete.")


async def render_panel(session_id: str, page_num: int, panel):
    logger.info(f"Rendering panel {panel.panel_number} for page {page_num}")
    prefix = f"comic_{session_id}_p{page_num}_pan{panel.panel_number}"
    prompt = generate_comfy_prompt(panel.image_prompt, prefix)
    res, client_id = await queue_prompt(prompt)
    prompt_id = res.get("prompt_id")
    if prompt_id:
        success = await wait_for_completion(prompt_id, client_id)
        if not success:
            logger.error(f"Panel {panel.panel_number} failed to render.")
        else:
            # ComfyUI appends a counter: <prefix>_00001_.png
            img_path = os.path.join(settings.comfy_output_dir, f"{prefix}_00001_.png")
            layout_bubbles(img_path, panel.speech_bubbles)
