import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app.config import settings

logger = logging.getLogger(__name__)

class NarrativeBeat(BaseModel):
    description: str = Field(..., description="Action or scene description")
    characters: List[str] = Field(..., description="Characters present in the beat")

class PageOutline(BaseModel):
    page_number: int
    beats: List[NarrativeBeat] = Field(..., description="3 to 5 narrative beats for this page")

class BeatSheet(BaseModel):
    pages: List[PageOutline]

class Panel(BaseModel):
    panel_number: int
    image_prompt: str = Field(..., description="SDXL prompt for the panel, focusing on visual details")
    character_focus: List[str] = Field(..., description="Characters prominently featured in this panel")
    speech_bubbles: List[str] = Field(..., description="Dialogue text to be placed in speech bubbles")
    caption: Optional[str] = Field(None, description="Narrator caption text, if any")

class PageDetail(BaseModel):
    page_number: int
    panels: List[Panel]

def generate_beat_sheet(transcript: str) -> BeatSheet:
    logger.info("Starting LLM Pass 1: Beat Sheet")
    client = genai.Client(api_key=settings.gemini_api_key)
    
    prompt = f"""
    You are an expert comic book writer. Read the following TTRPG transcript and outline a comic book issue of 8-10 pages.
    Each page should have 3 to 5 distinct narrative beats. Focus on the most important actions and dialogues.
    
    Transcript:
    {transcript}
    """
    
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=BeatSheet,
            temperature=0.4,
        ),
    )
    return BeatSheet.model_validate_json(response.text)

def generate_page_detail(page_outline: PageOutline, full_transcript: str) -> PageDetail:
    logger.info(f"Starting LLM Pass 2 for Page {page_outline.page_number}")
    client = genai.Client(api_key=settings.gemini_api_key)
    
    outline_str = "\n".join([f"- {b.description} (Characters: {', '.join(b.characters)})" for b in page_outline.beats])
    
    prompt = f"""
    You are an expert comic book script writer. You are writing page {page_outline.page_number}.
    Here is the outline for this page:
    {outline_str}
    
    Translate these beats into 3 to 5 comic book panels.
    For each panel, provide:
    - An 'image_prompt' suitable for a text-to-image AI (focus on visual composition, lighting, character actions).
    - A 'character_focus' list of which character(s) are the visual subject of the panel. Try to stick to 1 or 2 characters max to avoid blending.
    - 'speech_bubbles' with exact dialogue drawn from or inspired by the transcript context.
    - An optional 'caption'.
    """
    
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=PageDetail,
            temperature=0.4,
        ),
    )
    return PageDetail.model_validate_json(response.text)
