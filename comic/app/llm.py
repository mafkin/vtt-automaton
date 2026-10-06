"""Gemini calls for a comic: events from a transcript, a script, page drawings, lettering checks.

Every call returns its result and the tokens it used, so the caller can charge the comic's
budget (app.comics.charge). The prompt builders are plain functions so tests can check them.
"""

import difflib
import logging
import re

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.bible import Bible, Character
from app.comics import Moment, ScriptPage
from app.config import settings

logger = logging.getLogger(__name__)

# Lettering read back from a page counts as the script's balloon at this similarity (after
# normalising case and punctuation); between NEAR and MATCH it's a misspelling of it.
MATCH = 0.95
NEAR = 0.75


class EventsResult(BaseModel):
    events: str
    moments: list[Moment]


class ScriptResult(BaseModel):
    pages: list[ScriptPage]


class Lettering(BaseModel):
    texts: list[str]


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def _tokens(response) -> int:
    usage = getattr(response, "usage_metadata", None)
    return (usage.total_token_count or 0) if usage else 0


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


def events_prompt(transcript: str, bible: Bible) -> str:
    return f"""You read the automatic speech-recognition transcript of a tabletop RPG session.
It may have no speaker names, many recognition errors, and out-of-game chatter mixed in.

Campaign notes and the player characters:
{bible_context(bible) or "(none)"}

1. "events": what actually happened IN THE GAME, in order, as 8-15 short numbered events in
   English. For each: the player characters involved (exact names from the list), NPCs, the
   place, and whether it is funny or dramatic.
2. "moments": the 3-6 moments that would make the best one-page comic, each with a short title,
   a summary of what happens and why it works as a comic, and the player characters in it.
Do not invent events the transcript does not support.

Transcript:
{transcript}"""


def extract_events(transcript: str, bible: Bible) -> tuple[EventsResult, int]:
    response = _client().models.generate_content(
        model=settings.gemini_model,
        contents=events_prompt(transcript, bible),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=EventsResult,
            temperature=0.2,
        ),
    )
    return EventsResult.model_validate_json(response.text), _tokens(response)


def script_prompt(events: str, moments: list[Moment], bible: Bible) -> str:
    chosen = "\n".join(
        f"{i}. {m.title}: {m.summary} (characters: {', '.join(m.characters) or '-'})"
        for i, m in enumerate(moments, 1)
    )
    return f"""Write a comic: one page per moment below, in this order.

Campaign:
{bible_context(bible) or "(none)"}

What happened in the session:
{events}

Moments:
{chosen}

For each page:
- "title": a short, punchy title in {bible.bubble_language}.
- "characters": the player characters who appear (exact names from the campaign list).
- "panels": 4-6 panels. Each has "visual": a concrete description of the scene (where, who
  stands where doing what, camera angle, the place's architecture as the setting describes it),
  and "balloons": 0-2 speech balloons, each with "speaker" and "text".
Write fresh, punchy dialogue in {bible.bubble_language}, at most ~10 words per balloon. Don't
quote the transcript verbatim: it is full of recognition errors. Build each page to a payoff in
its last panel."""


def write_script(events: str, moments: list[Moment], bible: Bible) -> tuple[list[ScriptPage], int]:
    response = _client().models.generate_content(
        model=settings.gemini_model,
        contents=script_prompt(events, moments, bible),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ScriptResult,
            temperature=0.6,
        ),
    )
    return ScriptResult.model_validate_json(response.text).pages, _tokens(response)


def draw_prompt(page: ScriptPage, bible: Bible, cast: list[Character], extra: str = "") -> str:
    refs = "; ".join(
        f"Reference image {i} is {c.name.upper()}" + (f" ({c.appearance})" if c.appearance else "")
        for i, c in enumerate(cast, 1)
    )
    panels = "\n".join(
        f"Panel {i}: {p.visual}\n  Balloons: "
        + (" | ".join(f'{b.speaker}: "{b.text}"' for b in p.balloons) or "(none)")
        for i, p in enumerate(page.panels, 1)
    )
    return f"""Draw one finished comic page with {len(page.panels)} panels, clean gutters, and the
title "{page.title}" at the top, lettered exactly like that.
Art style: {bible.style.positive}. Avoid: {bible.style.negative}.
Setting: {bible.setting}
Characters: {refs or "(no references)"}. Keep their designs exactly as in the reference images
and consistent in every panel.
Letter every speech balloon exactly as written, character for character ({bible.bubble_language}),
in clear comic lettering with the tail pointing at the speaker. Each balloon appears once.
No captions or narration boxes, and no other text except sound effects.
{f"Extra instruction: {extra}" if extra else ""}

{panels}"""


def draw_page(
    page: ScriptPage, bible: Bible, cast: list[tuple[Character, bytes]], extra: str = ""
) -> tuple[bytes, int]:
    """One page image (PNG) from the script, steered by one reference image per character."""
    parts = [types.Part.from_bytes(data=png, mime_type="image/png") for _, png in cast]
    response = _client().models.generate_content(
        model=settings.gemini_image_model,
        contents=[*parts, draw_prompt(page, bible, [c for c, _ in cast], extra)],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="3:4"),
        ),
    )
    for part in response.candidates[0].content.parts if response.candidates else []:
        if part.inline_data and part.inline_data.data:
            return part.inline_data.data, _tokens(response)
    raise RuntimeError("Gemini returned no image")


def read_lettering(png: bytes) -> tuple[list[str], int]:
    """The speech balloon and caption texts on a page, as the model reads them."""
    response = _client().models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=png, mime_type="image/png"),
            "List the text of every speech balloon and caption box on this comic page, one "
            "entry per balloon or box, exactly as written. Leave out sound effects and the "
            "page title.",
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=Lettering, temperature=0
        ),
    )
    return Lettering.model_validate_json(response.text).texts, _tokens(response)


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def lettering_problems(page: ScriptPage, read: list[str]) -> list[str]:
    """What's wrong with the drawn lettering: missing/misspelled, drawn twice, or extra text."""
    expected = [b.text for p in page.panels for b in p.balloons]
    found = [0] * len(expected)
    extra = []
    for text in read:
        scores = [difflib.SequenceMatcher(None, _norm(text), _norm(e)).ratio() for e in expected]
        best = max(range(len(scores)), key=scores.__getitem__) if scores else None
        if best is not None and scores[best] >= MATCH:
            found[best] += 1
        elif best is None or scores[best] < NEAR:
            extra.append(text)
        # Between NEAR and MATCH: a misspelled balloon; it stays unfound and shows as missing.
    problems = [f"missing: {e}" for e, n in zip(expected, found, strict=True) if n == 0]
    problems += [f"twice: {e}" for e, n in zip(expected, found, strict=True) if n > 1]
    problems += [f"extra: {t}" for t in extra]
    return problems


def describe_prompt(name: str, notes: str = "") -> str:
    return (
        f"These are reference images of {name}, a character in a fantasy tabletop campaign. "
        "Describe how they look for a text-to-image prompt: species, build, hair, face, "
        "clothing, colours and signature items. Comma-separated phrases, at most 40 words, "
        "no names, no story. Leave out the pose, viewpoint, background and lighting of the "
        "images: the text is reused for every panel the character appears in."
        + (f" Notes from the players: {notes}" if notes else "")
    )


def describe_character(name: str, images: list[bytes], notes: str = "") -> str:
    """Draft a short visual description of a character from their reference images."""
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = describe_prompt(name, notes)
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[*parts, prompt],
        config=types.GenerateContentConfig(temperature=0.2),
    )
    return (response.text or "").strip()
