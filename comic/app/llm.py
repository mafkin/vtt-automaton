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
from app.scale import scale_line

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


def _client() -> genai.Client:
    """Keep the result in a variable for the whole call: the client closes its connection when
    it is garbage-collected, so "_client().models.generate_content(...)" fails."""
    # Retry overload and server errors ("503 UNAVAILABLE: high demand" is common for the image
    # model). Failed requests produce no tokens, so retries don't touch the budget.
    retry = types.HttpRetryOptions(
        attempts=4, initial_delay=5, max_delay=60, http_status_codes=[429, 500, 502, 503, 504]
    )
    return genai.Client(
        api_key=settings.gemini_api_key, http_options=types.HttpOptions(retry_options=retry)
    )


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
            if c.height_cm:
                look += f" (height {c.height_cm:g} cm)"
            spec = (f". Must have: {'; '.join(c.traits)}" if c.traits else "") + (
                f". Never: {'; '.join(c.never)}" if c.never else ""
            )
            lines.append(f"- {c.name}{also}{look}{spec}")
    return "\n".join(lines)


def events_prompt(transcript: str, bible: Bible) -> str:
    return f"""You read the automatic speech-recognition transcript of a tabletop RPG session.
It may have no speaker names, many recognition errors, and out-of-game chatter mixed in.
Lines written "Speaker (Character): text" come from a player speaking as that character: use the
character's name, not the player's.

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
    client = _client()
    response = client.models.generate_content(
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
Panel descriptions must agree with each character's must-haves and never list (weapons,
armour, clothing): the page drawer follows them. Call a signature item by the exact words its
must-have uses ("flail", "katana"), never by a generic or different word ("weapon", "sword",
"miekka"). Don't describe a character's body differently from the campaign list: keep their
posture (on two feet or on all fours), build and proportions, and the heights given.
Write fresh, punchy dialogue in {bible.bubble_language}, at most ~10 words per balloon. Don't
quote the transcript verbatim: it is full of recognition errors. Build each page to a payoff in
its last panel."""


def write_script(events: str, moments: list[Moment], bible: Bible) -> tuple[list[ScriptPage], int]:
    client = _client()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=script_prompt(events, moments, bible),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ScriptResult,
            temperature=0.6,
        ),
    )
    return ScriptResult.model_validate_json(response.text).pages, _tokens(response)


def _spec(c: Character) -> str:
    parts = []
    if c.traits:
        parts.append(f"{c.name.upper()} must have: {'; '.join(c.traits)}")
    if c.never:
        parts.append(f"{c.name.upper()} never: {'; '.join(c.never)}")
    if c.appearance:
        parts.append(f"Look: {c.appearance}")
    return ". ".join(parts)


def draw_prompt(
    page: ScriptPage,
    bible: Bible,
    cast: list[tuple[Character, list[str]]],
    extra: str = "",
    anchor: bool = False,
    previous: bool = False,
) -> str:
    """The page drawer's prompt. Images come in this order: each cast member's references (as
    labelled), then the style anchor page if `anchor`, then the previous page if `previous`."""
    lines, n = [], 0
    for c, labels in cast:
        for label in labels:
            n += 1
            lines.append(f"Reference image {n} is {c.name.upper()} ({label})")
        if spec := _spec(c):
            lines.append(spec)
    if anchor:
        n += 1
        lines.append(
            f"Reference image {n} is a STYLE REFERENCE page from the same comic: match its drawing"
            " style, colours, lettering and panel borders. Don't copy its characters, scene or text."
        )
    if previous:
        n += 1
        lines.append(
            f"Reference image {n} is the PREVIOUS PAGE of this comic: keep its rendering style and"
            " lettering. For how characters look, their own reference images and rules above win:"
            " where the previous page differs from them, follow the references, not the previous"
            " page. Don't copy its panels or text."
        )
    panels = "\n".join(
        f"Panel {i}: {p.visual}\n  Balloons: "
        + (" | ".join(f'{b.speaker}: "{b.text}"' for b in p.balloons) or "(none)")
        for i, p in enumerate(page.panels, 1)
    )
    refs = "\n".join(lines)
    sizes = scale_line(bible.match(page.characters))
    if sizes:
        refs += f"\nSizes (these win over the reference images, which aren't to scale): {sizes}"
    return f"""Draw one finished comic page with {len(page.panels)} panels, clean gutters, and the
title "{page.title}" at the top, lettered exactly like that.
Art style: {bible.style.positive}. Avoid: {bible.style.negative}.
Page look: {bible.style.page_look}.
Setting: {bible.setting}
Characters: keep their designs exactly as in their reference images, the same in every panel:
body shape and build, colours, and each signature item with exactly the number of parts and the
material its rule states (one flail head stays one; a steel blade stays steel).
Order of authority when inputs disagree: the character rules and sizes below, then each
character's reference images, then the panel descriptions, then any style or previous page.
{refs or "(no references)"}
Letter every speech balloon exactly as written, character for character ({bible.bubble_language}),
in clear comic lettering with the tail pointing at the speaker. Each balloon appears once.
No captions or narration boxes, and no other text except sound effects.
{f"Extra instruction: {extra}" if extra else ""}

{panels}"""


def draw_page(
    page: ScriptPage,
    bible: Bible,
    cast: list[tuple[Character, list[tuple[str, bytes]]]],
    extra: str = "",
    anchor: bytes | None = None,
    previous: bytes | None = None,
) -> tuple[bytes, int]:
    """One page image (PNG) from the script, steered by each character's labelled references,
    and optionally a style anchor page and the previous page of the comic."""
    images = [png for _, refs in cast for _, png in refs]
    images += [img for img in (anchor, previous) if img]
    parts = [types.Part.from_bytes(data=png, mime_type="image/png") for png in images]
    labels = [(c, [label for label, _ in refs]) for c, refs in cast]
    prompt = draw_prompt(page, bible, labels, extra, bool(anchor), bool(previous))
    client = _client()
    response = client.models.generate_content(
        model=settings.gemini_image_model,
        contents=[*parts, prompt],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="3:4"),
        ),
    )
    return _image_bytes(response), _tokens(response)


class LookProblem(BaseModel):
    character: str
    problem: str


class Inspection(BaseModel):
    texts: list[str]
    looks: list[LookProblem] = []


def inspect_prompt(cast: list[Character], look_check: bool) -> str:
    """Read the lettering back and, with `look_check`, find clear breaks of the characters' specs."""
    prompt = (
        '"texts": the text of every speech balloon and caption box on this comic page, one entry '
        "per balloon or box, exactly as written. Leave out sound effects and the page title."
    )
    specs = [c for c in cast if c.traits or c.never]
    sizes = scale_line(cast) if sum(1 for c in cast if c.height_cm) > 1 else ""
    if look_check and (specs or sizes):
        rules = "\n".join(
            f"- {c.name}: must have {'; '.join(c.traits) or '-'}; never {'; '.join(c.never) or '-'}"
            for c in specs
        )
        if sizes:
            rules += (
                f"\n- Sizes, where these characters stand side by side: {sizes} Report only a"
                " clear break, e.g. a character drawn far taller than stated."
            )
        prompt += f"""
"looks": for these characters, list only clear breaks of their rules that you can see on the
page, one entry per problem, naming the character. A detail that is not visible in a panel
(turned away, cropped, too small) is not a problem. Empty list if all is well.
{rules}"""
    else:
        prompt += ' "looks": always an empty list.'
    return prompt


def inspect_page(
    png: bytes, cast: list[Character], look_check: bool = True
) -> tuple[Inspection, int]:
    """One vision call per drawn page: the lettering, and (optionally) the characters' looks."""
    client = _client()
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=png, mime_type="image/png"),
            inspect_prompt(cast, look_check),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=Inspection, temperature=0
        ),
    )
    return Inspection.model_validate_json(response.text), _tokens(response)


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


class CharacterDraft(BaseModel):
    appearance: str
    traits: list[str]


def describe_prompt(name: str, notes: str = "") -> str:
    return (
        f"These are reference images of {name}, a character in a fantasy tabletop campaign. "
        '"appearance": how they look, for an image prompt: species, build, hair, face, clothing, '
        "colours and signature items; comma-separated phrases, at most 40 words, no names, no "
        'story. Describe build and proportions (e.g. "short, stubby, round body"), but not height '
        'in numbers: that is set separately. "traits": the 3-6 details that make them '
        "recognisable at a glance and must never change, each exact about shape, colour, "
        "material, number and position, naming each item precisely rather than generically "
        '(e.g. "flat-topped cylindrical great helm with a horizontal eye slit", "flail: exactly '
        'one spiked iron ball on one chain", "katana: one curved single-edged steel blade", '
        '"anthropomorphic seal walking upright on two short feet"). Leave out the pose, viewpoint, '
        "background and lighting of the images: the text is reused for every page the "
        "character appears in." + (f" Notes from the players: {notes}" if notes else "")
    )


def describe_character(
    name: str, images: list[bytes], notes: str = ""
) -> tuple[CharacterDraft, int]:
    """Draft a short visual description and must-have traits from the reference images."""
    client = _client()
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[*parts, describe_prompt(name, notes)],
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=CharacterDraft, temperature=0.2
        ),
    )
    return CharacterDraft.model_validate_json(response.text), _tokens(response)


def sheet_prompt(character: Character, bible: Bible) -> str:
    traits = "; ".join(character.traits)
    never = "; ".join(character.never)
    return f"""Draw a character model sheet of {character.name} for a comic: full body seen from
the front, in three-quarter view and from the side, in their natural stance as the reference
images show it (on two feet or on all fours, as they are), with the same proportions: short legs
stay short and a stubby body stays stubby; don't stretch or slim it. Side by side on a plain
light background. The same character, at the same size, in every view.
Look: {character.appearance or "as in the reference images"}.
{f"Must have: {traits}." if traits else ""}
{f"Never: {never}." if never else ""}
{f"Size: {size}" if (size := scale_line([character])) else ""}
Art style: {bible.style.positive}. Avoid: {bible.style.negative}.
The reference images show this character; keep their design, but draw it in the art style
above. No text, no labels, no other characters."""


def _image_bytes(response) -> bytes:
    for part in response.candidates[0].content.parts if response.candidates else []:
        if part.inline_data and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("Gemini returned no image")


def draw_sheet(character: Character, images: list[bytes], bible: Bible) -> tuple[bytes, int]:
    client = _client()
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in images]
    response = client.models.generate_content(
        model=settings.gemini_image_model,
        contents=[*parts, sheet_prompt(character, bible)],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9")
        ),
    )
    return _image_bytes(response), _tokens(response)


def detail_sheet_prompt(character: Character, bible: Bible) -> str:
    traits = "; ".join(character.traits)
    never = "; ".join(character.never)
    return f"""Draw a detail sheet of {character.name} for a comic: large close-ups, side by side
on a plain light background, of what makes this character recognisable: head or helm from the
front and from the side, any emblem or crest on the chest, the shield face, the main weapon,
signature items. Each detail big and sharp enough to copy exactly, with the same number of
parts and the same materials as in the references (don't add heads, blades or decorations).
The first image is the approved full-body sheet of {character.name}: the details must match it
exactly. Any further images are the original references.
{f"Must have: {traits}." if traits else ""}
{f"Never: {never}." if never else ""}
Art style: {bible.style.positive}. No text, no labels, no other characters."""


def draw_detail_sheet(
    character: Character, sheet: bytes, images: list[bytes], bible: Bible
) -> tuple[bytes, int]:
    client = _client()
    parts = [types.Part.from_bytes(data=img, mime_type="image/png") for img in [sheet, *images]]
    response = client.models.generate_content(
        model=settings.gemini_image_model,
        contents=[*parts, detail_sheet_prompt(character, bible)],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio="16:9")
        ),
    )
    return _image_bytes(response), _tokens(response)
