"""The comic steps the worker runs: extract events, write the script, draw pages.

Each step loads the comic, does its Gemini calls (counting their tokens; drawing is checked
against the comic's budget before every page), saves the result and leaves the comic in a
state the dashboard can show. A step never leaves the comic "busy": failures end as "failed",
an exhausted budget as "budget".
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app import bible as bible_store
from app import comics
from app.bible import Bible
from app.comics import BudgetExceeded, Comic, Moment, VersionInfo
from app.llm import draw_page, extract_events, inspect_page, lettering_problems, write_script
from app.sessions import transcript as get_transcript

logger = logging.getLogger(__name__)

# Tokens to have left before drawing a page. Measured on the server: ~5,500 per page (prompt with
# the cast's reference images, the image, the lettering check), rounded up.
PAGE_ESTIMATE = 6000


async def _run(comic_id: str, busy: str, step: Callable[[Comic], Awaitable[str]]) -> None:
    """Run one step: mark the comic busy, then save the state the step ends in."""
    comic = comics.load(comic_id)
    comic.status, comic.message = busy, ""
    comics.save(comic)
    try:
        comic.status = await step(comic)
    except BudgetExceeded as exc:
        comic.status, comic.message = "budget", str(exc)
    except Exception as exc:  # shown on the dashboard instead of a stuck "busy" comic
        logger.exception("Comic %s: %s failed", comic_id, busy)
        comic.status, comic.message = "failed", f"{type(exc).__name__}: {exc}"[:500]
    comics.save(comic)


async def extract(comic_id: str) -> None:
    async def step(comic: Comic) -> str:
        bible = bible_store.load()
        transcript = get_transcript(comic.session_id)
        if not transcript.strip():
            raise ValueError("The session has no transcript")
        result, tokens = await asyncio.to_thread(extract_events, transcript, bible)
        comics.charge(comic, tokens, "text")
        comic.events, comic.moments = result.events, result.moments
        return "events"

    await _run(comic_id, "extracting", step)


async def script(comic_id: str, chosen: list[int], own: str = "") -> None:
    async def step(comic: Comic) -> str:
        moments = [comic.moments[i] for i in chosen if 0 <= i < len(comic.moments)]
        if own.strip():
            moments.append(Moment(title=own.strip()))
        if not moments:
            raise ValueError("Choose at least one moment")
        pages, tokens = await asyncio.to_thread(
            write_script, comic.events, moments, bible_store.load()
        )
        comics.charge(comic, tokens, "text")
        comic.script, comic.pages = pages, []
        return "script"

    await _run(comic_id, "scripting", step)


async def draw(comic_id: str, page_index: int | None = None, extra: str = "") -> None:
    """Draw every page, or just one (a redraw, optionally with an extra instruction)."""

    async def step(comic: Comic) -> str:
        bible = bible_store.load()
        anchor = bible_store.anchor_image()
        indices = range(len(comic.script)) if page_index is None else [page_index]
        round_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{'all' if page_index is None else page_index}"
        for i in indices:
            await _draw_page(comic, i, bible, extra, anchor, round_id)
        return "done"

    await _run(comic_id, "drawing", step)


async def _draw_page(
    comic: Comic, index: int, bible: Bible, extra: str, anchor: bytes | None, round_id: str
) -> None:
    page = comic.script[index]
    characters = bible.match(page.characters)
    # Each character's labelled references: the approved sheet (or first image), then details.
    cast = [(c, refs) for c in characters if (refs := bible_store.references(c))]
    refs = {c.name: " + ".join(filter(None, [c.sheet or "image", c.detail])) for c, _ in cast}
    # Continuity: the page before this one as it is now (just drawn in this round, or current).
    previous_name = comic.pages[index - 1].current if 0 < index <= len(comic.pages) else None
    previous = comics.page_path(comic.id, previous_name).read_bytes() if previous_name else None
    limits = comics.load_limits()
    instruction = extra
    for attempt in range(1 + limits.max_auto_redraws_per_page):
        comics.ensure_budget(comic, PAGE_ESTIMATE)
        png, draw_tokens = await asyncio.to_thread(
            draw_page, page, bible, cast, instruction, anchor, previous
        )
        comics.charge(comic, draw_tokens, "image")
        info = VersionInfo(
            round=round_id,
            refs=refs,
            anchor=anchor is not None,
            previous=previous_name,
            extra=extra,
        )
        name = comics.add_page_version(comic, index, png, info)
        inspection, read_tokens = await asyncio.to_thread(
            inspect_page, png, characters, limits.look_check
        )
        comics.charge(comic, read_tokens, "image")  # part of drawing: it decides on redraws
        lettering = lettering_problems(page, inspection.texts)
        looks = (
            [f"{p.character}: {p.problem}" for p in inspection.looks] if limits.look_check else []
        )
        state = comic.pages[index]
        state.check = "; ".join(lettering) or "ok"
        state.looks = "; ".join(looks)
        info.check, info.looks = state.check, state.looks
        info.tokens = draw_tokens + read_tokens
        state.info[name] = info
        comics.save(comic)
        if not lettering and not looks:
            return
        # Redraw with what was wrong: re-rolling the same prompt brings the same mistakes back.
        fixes = "; ".join(looks + lettering)
        instruction = "\n".join(
            part for part in (extra, f"Fix these mistakes of the previous attempt: {fixes}") if part
        )
        logger.info(
            "Comic %s page %d attempt %d: %s", comic.id, index + 1, attempt + 1, lettering + looks
        )
