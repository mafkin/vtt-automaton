import logging

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.bible import Bible
from app.config import settings

logger = logging.getLogger(__name__)


class NarrativeBeat(BaseModel):
    description: str = Field(..., description="Action or scene description")
    characters: list[str] = Field(..., description="Characters present in the beat")


class PageOutline(BaseModel):
    page_number: int
    beats: list[NarrativeBeat] = Field(..., description="3 to 5 narrative beats for this page")


class BeatSheet(BaseModel):
    pages: list[PageOutline]


class Panel(BaseModel):
    panel_number: int
    image_prompt: str = Field(
        ..., description="SDXL prompt for the panel, focusing on visual details"
    )
    character_focus: list[str] = Field(
        ..., description="Characters prominently featured in this panel"
    )
    speech_bubbles: list[str] = Field(
        ..., description="Dialogue text to be placed in speech bubbles"
    )
    caption: str | None = Field(None, description="Narrator caption text, if any")


class PageDetail(BaseModel):
    page_number: int
    panels: list[Panel]


# Page range (min, max) of a production comic, and of a quick test run from the dashboard.
FULL_PAGES = (8, 10)
TEST_PAGES = (1, 2)


def bible_context(bible: Bible) -> str:
    """The campaign as the script writer should know it (setting, tone, recurring characters)."""
    lines = []
    if bible.setting:
        lines.append(f"Setting: {bible.setting}")
    if bible.tone:
        lines.append(f"Tone: {bible.tone}")
    if bible.characters:
        lines.append("Characters (use these exact names in character_focus):")
        for c in bible.characters:
            also = f" (also: {', '.join(c.aliases)})" if c.aliases else ""
            look = f": {c.appearance}" if c.appearance else ""
            lines.append(f"- {c.name}{also}{look}")
    return "\n".join(lines)


def beat_sheet_prompt(transcript: str, pages: tuple[int, int], context: str = "") -> str:
    return f"""
    You are an expert comic book writer. Read the following TTRPG transcript and outline a comic book issue of {pages[0]}-{pages[1]} pages.
    Each page should have 3 to 5 distinct narrative beats. Focus on the most important actions and dialogues.

    Campaign:
    {context or "(no campaign notes)"}

    Transcript:
    {transcript}
    """


def generate_beat_sheet(
    transcript: str, pages: tuple[int, int] = FULL_PAGES, context: str = ""
) -> BeatSheet:
    logger.info("Starting LLM Pass 1: Beat Sheet")
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = beat_sheet_prompt(transcript, pages, context)

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


def page_detail_prompt(
    page_outline: PageOutline, transcript: str, context: str = "", language: str = "Finnish"
) -> str:
    outline_str = "\n".join(
        [f"- {b.description} (Characters: {', '.join(b.characters)})" for b in page_outline.beats]
    )
    return f"""
    You are an expert comic book script writer. You are writing page {page_outline.page_number}.
    Here is the outline for this page:
    {outline_str}

    Campaign:
    {context or "(no campaign notes)"}

    Translate these beats into 3 to 5 comic book panels.
    For each panel, provide:
    - An 'image_prompt' suitable for a text-to-image AI (focus on visual composition, setting, lighting, character actions). The characters' looks are added automatically from the character sheet, so don't describe their clothes or faces.
    - A 'character_focus' list of which character(s) are the visual subject of the panel, using the exact names from the character list. Try to stick to 1 or 2 characters max to avoid blending.
    - 'speech_bubbles' with dialogue drawn from or inspired by the transcript, written in {language}.
    - An optional 'caption', written in {language}.

    Transcript:
    {transcript}
    """


def generate_page_detail(
    page_outline: PageOutline,
    full_transcript: str,
    context: str = "",
    language: str = "Finnish",
) -> PageDetail:
    logger.info(f"Starting LLM Pass 2 for Page {page_outline.page_number}")
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = page_detail_prompt(page_outline, full_transcript, context, language)

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


def describe_character(name: str, images: list[bytes], notes: str = "") -> str:
    """Draft a short visual description of a character from their reference images."""
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = (
        f"These are reference images of {name}, a character in a fantasy tabletop campaign. "
        "Describe how they look for a text-to-image prompt: species, build, hair, face, "
        "clothing, colours and signature items. Comma-separated phrases, at most 40 words, "
        "no names, no story." + (f" Notes from the players: {notes}" if notes else "")
    )
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[*parts, prompt],
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return (response.text or "").strip()
