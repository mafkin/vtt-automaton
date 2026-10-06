from app.comfy import generate_comfy_prompt, queue_prompt, wait_for_completion
import sqlite3
import logging
from app.llm import generate_beat_sheet, generate_page_detail
from app.gpu import gpu_lease
from app.config import settings
import httpx

logger = logging.getLogger(__name__)

def get_transcript(session_id: str) -> str:
    db_path = "/data/sessions.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT speaker, character, text FROM segments WHERE session_id = ? ORDER BY COALESCE(t_start, t_end), id",
        (session_id,)
    ).fetchall()
    
    lines = []
    for r in rows:
        who = f"{r['speaker']} ({r['character']})" if r['character'] else r['speaker']
        lines.append(f"{who}: {r['text']}")
    return "\n".join(lines)

async def run_pipeline(session_id: str):
    transcript = get_transcript(session_id)
    if not transcript.strip():
        logger.warning(f"No transcript found for session {session_id}")
        return
        
    logger.info(f"Loaded transcript for {session_id}, length: {len(transcript)}")
    
    # Pass 1: Beat Sheet
    # Note: async LLM calls would be better but the python genai SDK is mostly sync or requires async setup.
    # For a background worker, sync is fine, or we run it in a threadpool.
    # Actually, we can run them in a threadpool to not block the event loop.
    import asyncio
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
    # TODO: Call ComfyUI over HTTP & WS watchdog
    prefix = f"comic_{session_id}_p{page_num}_pan{panel.panel_number}"
    prompt = generate_comfy_prompt(panel.image_prompt, prefix)
    res, client_id = await queue_prompt(prompt)
    prompt_id = res.get("prompt_id")
    if prompt_id:
        success = await wait_for_completion(prompt_id, client_id)
        if not success:
            logger.error(f"Panel {panel.panel_number} failed to render.")
        else:
            # Try to layout bubbles
            import os
            from app.layout import layout_bubbles
            # ComfyUI usually appends _00001_.png
            img_path = f"/comfy_output/{prefix}_00001_.png"
            layout_bubbles(img_path, panel.speech_bubbles)

