import contextlib
import sqlite3
from collections.abc import AsyncIterator
from html import escape
from typing import Annotated
from urllib.parse import quote, urlsplit

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response

from app import bible, comics, views
from app.bible import BibleError
from app.config import settings
from app.llm import describe_character, draw_detail_sheet, draw_sheet
from app.scale import parse_height
from app.sessions import (
    character_tags,
    ended_sessions_with_transcripts,
    get_session,
    recent_sessions,
    transcript,
)
from app.templating import templates


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Tests put a fake queue here before starting the app.
    if getattr(app.state, "queue", None) is None:
        app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield


app = FastAPI(title="VTT Comic API", lifespan=lifespan)
app.include_router(views.router)

# Transcript lines shown when picking a session.
PREVIEW_LINES = 10


@app.middleware("http")
async def same_site_posts_only(request: Request, call_next):
    """The dashboard has no login (it listens on 127.0.0.1 only), so refuse POSTs that another
    website makes the browser send: they could switch services off or spend Gemini tokens."""
    if request.method == "POST":
        fetch_site = request.headers.get("sec-fetch-site")
        origin = request.headers.get("origin")
        if fetch_site == "cross-site" or (origin and urlsplit(origin).netloc != request.url.netloc):
            return PlainTextResponse("Cross-site request refused", status.HTTP_403_FORBIDDEN)
    return await call_next(request)


@app.get("/api/v1/dashboard/sessions", response_class=HTMLResponse)
async def get_sessions() -> str:
    try:
        sessions = recent_sessions(5)
    except sqlite3.Error as exc:
        return f"<p class='text-red-500'>Error loading sessions: {escape(str(exc))}</p>"
    if not sessions:
        return "<p class='p-4 text-sm text-slate-500'>No sessions yet. Start one in Discord.</p>"
    items = []
    for s in sessions:
        state = (
            "<span class='inline-flex items-center gap-1.5 rounded-full bg-rose-500/10 px-2 py-0.5"
            " text-xs font-medium text-rose-300 ring-1 ring-inset ring-rose-500/30'>"
            "<span class='h-1.5 w-1.5 animate-pulse rounded-full bg-current'></span>Live</span>"
            if s.live
            else "<span class='text-xs text-slate-500'>Ended</span>"
        )
        items.append(
            "<li class='flex items-center justify-between gap-3 rounded-xl px-3 py-2.5"
            " hover:bg-white/5'>"
            f"<div class='min-w-0'><div class='truncate font-medium text-slate-100'>"
            f"{escape(s.label or 'Unnamed session')}</div>"
            f"<div class='truncate text-xs text-slate-500'>{escape(s.id)}</div></div>{state}</li>"
        )
    return "<ul class='divide-y divide-white/5'>" + "".join(items) + "</ul>"


@app.get("/api/v1/dashboard/transcript", response_class=HTMLResponse)
async def get_transcript_preview(session_id: str) -> str:
    if get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    lines = transcript(session_id).splitlines()
    more = f"\n… {len(lines) - PREVIEW_LINES} more lines" if len(lines) > PREVIEW_LINES else ""
    # Speaker tags (/link) are how the comic knows who is who: show which match the bible.
    known = bible.load()
    tags = [
        f"{'✓' if known.match([name]) else '✗'} {name} ({n})"
        for name, n in character_tags(session_id)
    ]
    header = ""
    if tags:
        header = "Characters: " + " · ".join(tags)
        if any(t.startswith("✗") for t in tags):
            header += "\n✗ = not in the bible: add it as a character name or an alias."
        header += "\n\n"
    return escape(header + "\n".join(lines[:PREVIEW_LINES]) + more)


# --- comics ----------------------------------------------------------------------------------


def _comic_view(request: Request, comic: comics.Comic, notice: str = "") -> HTMLResponse:
    context = {
        "c": comic,
        "busy": comic.status in comics.BUSY,
        "budget": comics.load_limits().token_budget_per_comic,
        "notice": notice,
        "rounds": comics.rounds(comic),
    }
    return templates.TemplateResponse(request, "comic.html", context)


def _load(comic_id: str) -> comics.Comic:
    try:
        return comics.load(comic_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown comic") from exc


def _error(message: str) -> HTMLResponse:
    return HTMLResponse(f"<p class='text-red-400 font-bold'>{escape(message)}</p>")


@app.get("/api/v1/comics", response_class=HTMLResponse)
async def get_comics(request: Request):
    try:
        sessions = ended_sessions_with_transcripts()
    except sqlite3.Error as exc:
        return _error(f"Error loading transcripts: {exc}")
    context = {
        "sessions": sessions,
        "comics": comics.list_comics(),
        "budget": comics.load_limits().token_budget_per_comic,
        "busy": comics.BUSY,
    }
    return templates.TemplateResponse(request, "comics.html", context)


@app.post("/api/v1/comics", response_class=HTMLResponse)
async def start_comic(request: Request, session_id: str = Form("")):
    session = get_session(session_id)
    if session is None:
        return _error("Unknown session")
    if session.live:
        return _error("Session is still recording; stop it first (/session stop)")
    comic = comics.create(session.id, session.label or session.id)
    comic.status = "extracting"
    comics.save(comic)
    await request.app.state.queue.enqueue_job(
        "extract_job", comic.id, _job_id=f"comic:{comic.id}:extract"
    )
    return _comic_view(request, comic)


@app.get("/api/v1/comics/{comic_id}", response_class=HTMLResponse)
async def get_comic(request: Request, comic_id: str):
    return _comic_view(request, _load(comic_id))


async def _queue_step(
    request: Request, comic: comics.Comic, busy: str, job: str, *args, key: str
) -> HTMLResponse:
    """Mark the comic busy and queue a worker step, unless it is already working."""
    if comic.status in comics.BUSY:
        return _comic_view(request, comic, "This comic is already working; wait for it.")
    comic.status, comic.message = busy, ""
    comics.save(comic)
    await request.app.state.queue.enqueue_job(job, comic.id, *args, _job_id=f"comic:{key}")
    return _comic_view(request, comic)


@app.post("/api/v1/comics/{comic_id}/script", response_class=HTMLResponse)
async def write_comic_script(
    request: Request,
    comic_id: str,
    chosen: Annotated[list[int] | None, Form()] = None,
    own: str = Form(""),
):
    comic = _load(comic_id)
    chosen = chosen or []
    if not chosen and not own.strip():
        return _comic_view(request, comic, "Choose at least one moment, or describe your own.")
    return await _queue_step(
        request, comic, "scripting", "script_job", chosen, own.strip(), key=f"{comic.id}:script"
    )


def _balloons(text: str) -> list[comics.Balloon]:
    """One balloon per line, "Speaker: text"."""
    balloons = []
    for line in text.splitlines():
        speaker, sep, said = line.partition(":")
        if line.strip():
            balloons.append(
                comics.Balloon(speaker=speaker.strip(), text=said.strip())
                if sep
                else comics.Balloon(speaker="", text=line.strip())
            )
    return balloons


def _apply_script_edits(comic: comics.Comic, form) -> None:
    """Copy the dashboard's script editor fields (if sent) into the comic."""
    for i, page in enumerate(comic.script):
        page.title = str(form.get(f"page-{i}-title", page.title)).strip() or page.title
        names = str(form.get(f"page-{i}-characters", ", ".join(page.characters)))
        page.characters = [n.strip() for n in names.split(",") if n.strip()]
        for j, panel in enumerate(page.panels):
            panel.visual = str(form.get(f"page-{i}-panel-{j}-visual", panel.visual)).strip()
            key = f"page-{i}-panel-{j}-balloons"
            if key in form:
                panel.balloons = _balloons(str(form[key]))


@app.post("/api/v1/comics/{comic_id}/script/save", response_class=HTMLResponse)
async def save_comic_script(request: Request, comic_id: str):
    comic = _load(comic_id)
    if comic.status in comics.BUSY:
        return _comic_view(request, comic, "This comic is already working; wait for it.")
    _apply_script_edits(comic, await request.form())
    comics.save(comic)
    return _comic_view(request, comic, "Script saved.")


@app.post("/api/v1/comics/{comic_id}/draw", response_class=HTMLResponse)
async def draw_comic(request: Request, comic_id: str):
    """Draw all pages, or one ("page"). The buttons sit in the script editor, so its fields
    come along: unsaved edits are saved first, and a page's redraw instruction is extra-<n>."""
    comic = _load(comic_id)
    if not comic.script:
        return _comic_view(request, comic, "Write the script first.")
    form = await request.form()
    raw_page = str(form.get("page", "")).strip()
    page = int(raw_page) if raw_page.isdigit() else None
    if page is not None and page >= len(comic.script):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown page")
    extra = str(form.get("extra") or form.get(f"extra-{page}") or "").strip()
    if comic.status not in comics.BUSY:
        _apply_script_edits(comic, form)
    key = f"{comic.id}:draw" if page is None else f"{comic.id}:draw:{page}"
    return await _queue_step(request, comic, "drawing", "draw_job", page, extra, key=key)


@app.get("/api/v1/comics/{comic_id}/pages/{name}")
async def get_page(comic_id: str, name: str):
    try:
        path = comics.page_path(comic_id, name)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown page") from exc
    return FileResponse(path, media_type="image/png")


@app.get("/api/v1/limits", response_class=HTMLResponse)
async def get_limits(request: Request):
    return templates.TemplateResponse(request, "limits.html", {"limits": comics.load_limits()})


@app.post("/api/v1/limits", response_class=HTMLResponse)
async def save_limits(
    request: Request,
    token_budget_per_comic: int = Form(...),
    max_auto_redraws_per_page: int = Form(...),
    look_check: str | None = Form(None),  # a checkbox: absent when unticked
    page_reference_images: int = Form(1),
):
    limits, message = comics.load_limits(), "Saved."
    if (
        token_budget_per_comic < 0
        or not 0 <= max_auto_redraws_per_page <= 3
        or not 0 <= page_reference_images <= 3
    ):
        message = "The budget must be 0 or more; automatic redraws and own images 0-3."
    else:
        limits = comics.Limits(
            token_budget_per_comic=token_budget_per_comic,
            max_auto_redraws_per_page=max_auto_redraws_per_page,
            look_check=look_check is not None,
            page_reference_images=page_reference_images,
        )
        comics.save_limits(limits)
    context = {"limits": limits, "message": message}
    return templates.TemplateResponse(request, "limits.html", context)


# --- comic bible (the Characters pages) ------------------------------------------------------


def _media(request: Request, character_id: str, message: str = "", level: str = "success"):
    return views.character_fragment(request, character_id, "char_media.html", message, level)


@app.post("/api/v1/bible/campaign", response_class=HTMLResponse)
async def save_campaign(
    request: Request,
    setting: str = Form(""),
    tone: str = Form(""),
    bubble_language: str = Form("Finnish"),
    style_positive: str = Form(""),
    style_negative: str = Form(""),
    style_page_look: str = Form(""),
):
    b = bible.load()
    b.setting, b.tone = setting.strip(), tone.strip()
    b.bubble_language = bubble_language.strip() or "Finnish"
    b.style.positive, b.style.negative = style_positive.strip(), style_negative.strip()
    b.style.page_look = style_page_look.strip()
    bible.save(b)
    return views.campaign_fragment(request, "Campaign and style saved.")


@app.post("/api/v1/bible/characters")
async def add_character(name: str = Form("")):
    if not name.strip():
        return views.toast_only("Name is required.")
    c = bible.add_character(name)
    return views.redirect(f"/characters/{c.id}?notice={quote(f'Added {c.name}.')}")


@contextlib.contextmanager
def _known_character():
    """Unknown character or image: 404, like a missing page."""
    try:
        yield
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown character or image") from exc


@app.post("/api/v1/bible/characters/{character_id}", response_class=HTMLResponse)
async def update_character(
    request: Request,
    character_id: str,
    name: str = Form(""),
    aliases: str = Form(""),
    appearance: str = Form(""),
    traits: str | None = Form(None),
    never: str | None = Form(None),
    height: str | None = Form(None),
    # Sent by the profile form next to "height": FastAPI turns an empty field into "not sent",
    # so this tells "cleared" apart from a client that doesn't send heights at all.
    height_field: str | None = Form(None),
):
    try:
        height_cm = parse_height(height or "") if height_field else "keep"
    except ValueError as exc:
        return views.toast_only(str(exc))
    with _known_character():
        bible.update_character(
            character_id,
            name,
            aliases.split(","),
            appearance,
            traits.splitlines() if traits is not None else None,
            never.splitlines() if never is not None else None,
            height_cm,
        )
    return views.character_fragment(request, character_id, "char_profile.html", "Profile saved.")


@app.post("/api/v1/bible/characters/{character_id}/delete")
async def delete_character(character_id: str):
    with _known_character():
        name = bible.load().character(character_id).name
        bible.delete_character(character_id)
    return views.redirect(f"/characters?notice={quote(f'Deleted {name}.')}")


@app.post("/api/v1/bible/characters/{character_id}/images", response_class=HTMLResponse)
async def upload_images(request: Request, character_id: str, files: list[UploadFile]):
    stored = 0
    with _known_character():
        for f in files:
            # Read one byte past the limit so an oversize file is refused, not truncated.
            data = await f.read(bible.MAX_UPLOAD_BYTES + 1)
            if not data:
                continue
            try:
                await run_in_threadpool(bible.add_image, character_id, data)
            except BibleError as exc:
                done = f" ({stored} uploaded before it)" if stored else ""
                return _media(request, character_id, f"{f.filename}: {exc}{done}", "error")
            stored += 1
    return _media(request, character_id, f"Uploaded {stored} image{'s' if stored != 1 else ''}.")


def _image_response(path, w: int | None):
    """The stored PNG, or a cached JPEG thumbnail (?w=) for the dashboard's grids."""
    if w:
        data = bible.thumbnail(path, w)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})
    return FileResponse(path, media_type="image/png")


@app.get("/api/v1/bible/characters/{character_id}/images/{name}")
async def get_image(character_id: str, name: str, w: int | None = None):
    with _known_character():
        path = bible.image_path(character_id, name)
    return await run_in_threadpool(_image_response, path, w)


@app.post(
    "/api/v1/bible/characters/{character_id}/images/{name}/first", response_class=HTMLResponse
)
async def move_image_first(request: Request, character_id: str, name: str):
    with _known_character():
        bible.move_image_first(character_id, name)
    return _media(request, character_id, "Image moved first: it now goes with every page.")


@app.post(
    "/api/v1/bible/characters/{character_id}/images/{name}/delete", response_class=HTMLResponse
)
async def delete_image(request: Request, character_id: str, name: str):
    with _known_character():
        bible.delete_image(character_id, name)
    return _media(request, character_id, "Image removed.")


@app.post("/api/v1/bible/characters/{character_id}/describe", response_class=HTMLResponse)
async def draft_appearance(
    request: Request, character_id: str, appearance: str = Form(""), traits: str = Form("")
):
    """Let Gemini draft the appearance and must-have traits from the reference images.
    Not saved until Save."""
    with _known_character():
        character = bible.load().character(character_id)
        images = [bible.image_path(character_id, n).read_bytes() for n in character.images]

    def fragment(text: str, trait_list: list[str], note: str, error: bool = False):
        context = {
            "c": character,
            "appearance": text,
            "traits": trait_list,
            "note": note,
            "error": error,
        }
        return templates.TemplateResponse(request, "appearance.html", context)

    current = [t for t in traits.splitlines() if t.strip()]
    if not images:
        return fragment(appearance, current, "Upload reference images first.", error=True)
    try:
        draft, tokens = await run_in_threadpool(
            describe_character, character.name, images, appearance
        )
    except Exception as exc:  # Gemini errors vary; show them instead of a 500
        return fragment(appearance, current, f"Gemini failed: {exc}", error=True)
    bible.charge(tokens)
    note = "Draft from the images. Edit it, then press Save profile to keep it."
    return fragment(draft.appearance, draft.traits, note)


@app.post("/api/v1/bible/characters/{character_id}/sheets", response_class=HTMLResponse)
async def draw_character_sheet(request: Request, character_id: str):
    """Draw a character sheet in the comic's style from the reference images (~30 s)."""
    with _known_character():
        b = bible.load()
        character = b.character(character_id)
        images = [bible.image_path(character_id, n).read_bytes() for n in character.images]
    if not images:
        return _media(request, character_id, "Upload reference images first.", "error")
    try:
        png, tokens = await run_in_threadpool(draw_sheet, character, images, b)
    except Exception as exc:  # Gemini errors vary; show them instead of a 500
        return _media(request, character_id, f"Gemini failed: {exc}", "error")
    bible.charge(tokens)
    bible.add_sheet(character_id, png)
    return _media(request, character_id, "New sheet drawn. Approve it if it matches the images.")


@app.post("/api/v1/bible/characters/{character_id}/details", response_class=HTMLResponse)
async def draw_character_details(request: Request, character_id: str):
    """Draw close-ups of a character's details from their approved sheet (~30 s)."""
    with _known_character():
        b = bible.load()
        character = b.character(character_id)
        images = [bible.image_path(character_id, n).read_bytes() for n in character.images]
    if not character.sheet:
        return _media(request, character_id, "Approve a character sheet first.", "error")
    sheet = bible.sheet_path(character_id, character.sheet).read_bytes()
    try:
        png, tokens = await run_in_threadpool(draw_detail_sheet, character, sheet, images, b)
    except Exception as exc:  # Gemini errors vary; show them instead of a 500
        return _media(request, character_id, f"Gemini failed: {exc}", "error")
    bible.charge(tokens)
    bible.add_sheet(character_id, png, kind="detail")
    return _media(request, character_id, "New detail sheet drawn. Approve it if it matches.")


@app.get("/api/v1/bible/characters/{character_id}/sheets/{name}")
async def get_sheet(character_id: str, name: str, w: int | None = None):
    with _known_character():
        path = bible.sheet_path(character_id, name)
    return await run_in_threadpool(_image_response, path, w)


@app.post(
    "/api/v1/bible/characters/{character_id}/sheets/{name}/approve", response_class=HTMLResponse
)
async def approve_sheet(request: Request, character_id: str, name: str):
    with _known_character():
        bible.approve_sheet(character_id, name)
    return _media(request, character_id, "Approved: pages now use this sheet.")


@app.post(
    "/api/v1/bible/characters/{character_id}/sheets/{name}/delete", response_class=HTMLResponse
)
async def delete_sheet(request: Request, character_id: str, name: str):
    with _known_character():
        bible.delete_sheet(character_id, name)
    return _media(request, character_id, "Sheet deleted.")


@app.get("/api/v1/bible/anchor")
async def get_anchor():
    png = bible.anchor_image()
    if png is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No style reference")
    return Response(png, media_type="image/png")


@app.post("/api/v1/bible/anchor/delete", response_class=HTMLResponse)
async def delete_anchor(request: Request):
    bible.clear_anchor()
    return views.campaign_fragment(request, "Style reference removed.")


@app.post("/api/v1/comics/{comic_id}/pages/{name}/anchor", response_class=HTMLResponse)
async def use_as_anchor(comic_id: str, name: str):
    """Make a drawn page the style reference for every page from now on."""
    try:
        png = comics.page_path(comic_id, name).read_bytes()
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown page") from exc
    bible.set_anchor(png)
    return HTMLResponse("<span class='text-green-400 font-bold'>Style reference set.</span>")
