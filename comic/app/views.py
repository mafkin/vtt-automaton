"""Dashboard pages (Server, Comics, Characters) and the helpers their fragments share.

Pages are full HTML documents built on base.html. Actions answer with the fragment that
changed (htmx swaps it in), plus a toast through the HX-Trigger header.
"""

import json
import sqlite3
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app import bible, comics, services
from app.bible import Character
from app.sessions import recent_sessions
from app.templating import templates

router = APIRouter()


def toast(response: Response, message: str, level: str = "success") -> Response:
    """Show a notification on the page (base.html listens for the "toast" event)."""
    response.headers["HX-Trigger"] = json.dumps({"toast": {"message": message, "level": level}})
    return response


def toast_only(message: str, level: str = "error") -> Response:
    """A notification and nothing else: the page keeps what it shows (e.g. a refused save)."""
    return toast(Response(headers={"HX-Reswap": "none"}), message, level)


def redirect(url: str) -> Response:
    """Send the browser elsewhere after an htmx action (e.g. to a new character's page)."""
    return Response(headers={"HX-Redirect": url})


# --- characters ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    label: str
    done: bool
    required: bool
    hint: str
    anchor: str


def _base(c: Character) -> str:
    return f"/api/v1/bible/characters/{c.id}"


def readiness(c: Character) -> list[Step]:
    """What a character still needs before comics draw them well, in the order to do it."""
    return [
        Step(
            "Reference image",
            bool(c.images),
            True,
            "Upload a clear picture of them alone.",
            "images",
        ),
        Step(
            "Height",
            c.height_cm is not None,
            True,
            "Pages compare the characters' sizes.",
            "profile",
        ),
        Step(
            "Description",
            bool(c.appearance.strip() or c.traits),
            True,
            "Appearance or must-haves; Draft from images helps.",
            "profile",
        ),
        Step(
            "Approved sheet",
            bool(c.sheet),
            False,
            "Pages follow it closely: draw one, approve it.",
            "sheets",
        ),
        Step("Detail sheet", bool(c.detail), False, "Close-ups of insignia and weapons.", "sheets"),
    ]


def page_refs(c: Character, own_images: int) -> str:
    """What every comic page gets of this character (bible.references, in words)."""
    parts = [
        label
        for label, name in (("the approved sheet", c.sheet), ("the detail sheet", c.detail))
        if name
    ]
    n = min(len(c.images), own_images if c.sheet else max(own_images, 1))
    if n:
        parts.append(f"{n} reference image{'s' if n > 1 else ''}")
    return ", ".join(parts) if parts else "nothing yet: upload a reference image."


def character_context(c: Character) -> dict:
    own_images = comics.load_limits().page_reference_images
    steps = readiness(c)
    base = _base(c)
    hero = None
    # The approved sheet shows the design pages follow; else the first reference image.
    if c.sheet:
        full, caption = f"{base}/sheets/{c.sheet}", f"{c.name}: approved sheet"
    elif c.images:
        full, caption = f"{base}/images/{c.images[0]}", f"{c.name}: first reference image"
    if c.sheet or c.images:
        hero = {"full": full, "thumb": f"{full}?w=640", "caption": caption}
    return {
        "c": c,
        "steps": steps,
        "done": sum(s.done for s in steps),
        "ready": all(s.done for s in steps if s.required),
        "hero": hero,
        "on_pages": own_images if c.sheet else max(own_images, 1),
        "page_refs": page_refs(c, own_images),
    }


def card(c: Character) -> dict:
    steps = readiness(c)
    avatar = None
    if c.images:
        avatar = f"{_base(c)}/images/{c.images[0]}?w=320"
    elif c.sheet:
        avatar = f"{_base(c)}/sheets/{c.sheet}?w=640"
    return {
        "c": c,
        "avatar": avatar,
        "ready": all(s.done for s in steps if s.required),
        "done": sum(s.done for s in steps),
        "total": len(steps),
    }


def character_fragment(
    request: Request, character_id: str, fragment: str, message: str = "", level: str = "success"
) -> Response:
    """char_profile.html or char_media.html for one character, with the summary card
    refreshed out of band (readiness and the hero image follow every change)."""
    try:
        c = bible.load().character(character_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown character") from exc
    context = {**character_context(c), "summary_oob": True}
    response = templates.TemplateResponse(request, fragment, context)
    return toast(response, message, level) if message else response


def campaign_fragment(request: Request, message: str = "", level: str = "success") -> Response:
    response = templates.TemplateResponse(request, "campaign_form.html", {"bible": bible.load()})
    return toast(response, message, level) if message else response


# --- pages -----------------------------------------------------------------------------------


def _page(request: Request, name: str, page: str, title: str, notice: str = "", **context):
    context = {"page": page, "title": title, "notice": notice, **context}
    return templates.TemplateResponse(request, name, context)


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/dashboard")


@router.get("/dashboard", response_class=HTMLResponse)
async def server_page(request: Request, notice: str = ""):
    return _page(request, "server.html", "server", "Server", notice)


@router.get("/comics", response_class=HTMLResponse)
async def comics_page(request: Request, notice: str = ""):
    return _page(request, "comics_page.html", "comics", "Comics", notice)


@router.get("/characters", response_class=HTMLResponse)
async def characters_page(request: Request, notice: str = ""):
    cards = [card(c) for c in bible.load().characters]
    return _page(
        request, "characters.html", "characters", "Characters", notice, tab="cast", cards=cards
    )


@router.get("/characters/campaign", response_class=HTMLResponse)
async def campaign_page(request: Request, notice: str = ""):
    b = bible.load()
    return _page(
        request, "campaign.html", "characters", "Campaign & style", notice, tab="campaign", bible=b
    )


@router.get("/characters/{character_id}", response_class=HTMLResponse)
async def character_page(request: Request, character_id: str, notice: str = ""):
    try:
        c = bible.load().character(character_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown character") from exc
    return _page(request, "character.html", "characters", c.name, notice, **character_context(c))


# --- server: services that can be switched on and off ----------------------------------------


async def services_fragment(
    request: Request, message: str = "", level: str = "success"
) -> Response:
    error = ""
    try:
        found = await services.containers()
    except services.ServiceError as exc:
        found, error = [], str(exc)
    groups = services.group_states(found)
    # Switching a group off also stops the running groups that need it: say which.
    dependants = {
        g.group.id: ", ".join(d.group.name for d in groups if g.group.id in d.group.needs and d.on)
        for g in groups
    }
    try:
        live = any(s.live for s in recent_sessions(5))
    except sqlite3.Error:
        live = False
    context = {"groups": groups, "error": error, "live": live, "dependants": dependants}
    response = templates.TemplateResponse(request, "services.html", context)
    return toast(response, message, level) if message else response


@router.get("/api/v1/services", response_class=HTMLResponse)
async def get_services(request: Request):
    return await services_fragment(request)


@router.post("/api/v1/services/{group_id}/{action}", response_class=HTMLResponse)
async def switch_service(request: Request, group_id: str, action: str):
    if action not in ("on", "off"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Use on or off")
    try:
        group = services.group(group_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown service group") from exc
    try:
        await services.switch(group_id, action == "on")
    except services.ServiceError as exc:
        return await services_fragment(request, str(exc), "error")
    return await services_fragment(request, f"{group.name} switched {action}.")
