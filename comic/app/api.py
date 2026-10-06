import contextlib
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime
from html import escape
from urllib.parse import parse_qs

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app import bible
from app.bible import BibleError
from app.config import settings
from app.gpu import lock_info
from app.llm import describe_character
from app.sessions import (
    any_live,
    ended_sessions_with_transcripts,
    get_session,
    recent_sessions,
    transcript,
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Tests put a fake queue here before starting the app.
    if getattr(app.state, "queue", None) is None:
        app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield


app = FastAPI(title="VTT Comic API", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"

# Transcript lines shown when picking a session.
PREVIEW_LINES = 10


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/v1/dashboard/containers", response_class=HTMLResponse)
async def get_containers() -> str:
    try:
        async with httpx.AsyncClient() as client:
            # all=true: ComfyUI is stopped between comics and should still be listed.
            r = await client.get(
                f"{settings.docker_proxy_url}/containers/json",
                params={"all": "true"},
                timeout=10.0,
            )
            r.raise_for_status()
            containers = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"<p class='text-red-500'>Error loading containers: {escape(str(exc))}</p>"
    items = []
    for c in containers:
        # The proxy sees every container on the host; show only this compose project.
        if (c.get("Labels") or {}).get(COMPOSE_PROJECT_LABEL) != settings.compose_project:
            continue
        name = escape(c["Names"][0].lstrip("/"))
        state = escape(c["State"])
        color = "text-green-400" if c["State"] == "running" else "text-red-400"
        if name == settings.comfyui_container_name and c["State"] != "running":
            state, color = f"{state} – starts for comics", "text-slate-400"
        items.append(
            "<li class='flex justify-between border-b border-slate-700 pb-2'>"
            f"<span>{name}</span><span class='{color}'>{state}</span></li>"
        )
    return "<ul class='space-y-2'>" + "".join(items) + "</ul>"


@app.get("/api/v1/dashboard/sessions", response_class=HTMLResponse)
async def get_sessions() -> str:
    try:
        sessions = recent_sessions(5)
    except sqlite3.Error as exc:
        return f"<p class='text-red-500'>Error loading sessions: {escape(str(exc))}</p>"
    items = []
    for s in sessions:
        state = (
            "<span class='text-amber-400'>Live</span>"
            if s.live
            else "<span class='text-slate-400'>Ended</span>"
        )
        items.append(
            "<li class='bg-slate-700 p-4 rounded flex items-center justify-between'>"
            f"<div><div class='font-bold text-white'>{escape(s.label or 'Unnamed Session')}</div>"
            f"<div class='text-sm text-slate-400'>ID: {escape(s.id)}</div></div>{state}</li>"
        )
    return "<ul class='space-y-4'>" + "".join(items) + "</ul>"


def _blocked_reason() -> str | None:
    """Why no comic can start right now, or None."""
    if lock_info() is not None:
        return "Comic in progress"
    if any_live():
        return "Session live"
    return None


@app.get("/api/v1/dashboard/mode", response_class=HTMLResponse)
async def get_mode() -> str:
    lock = lock_info()
    if lock is None:
        return "<span class='text-green-400 font-bold'>Idle</span> – transcription available"
    session = escape(str(lock.get("session_id") or "unknown session"))
    return (
        f"<span class='text-amber-400 font-bold'>Comic mode</span> – rendering {session}, "
        "transcription unavailable until it finishes"
    )


@app.get("/api/v1/dashboard/picker", response_class=HTMLResponse)
async def get_picker() -> str:
    try:
        choices = ended_sessions_with_transcripts()
        blocked = _blocked_reason()
    except sqlite3.Error as exc:
        return f"<p class='text-red-500'>Error loading transcripts: {escape(str(exc))}</p>"
    if not choices:
        return "<p class='text-slate-400'>No finished sessions with a transcript yet.</p>"
    options = "".join(
        f"<option value='{escape(c.id)}'>{escape(c.label or 'Unnamed Session')} – "
        f"{datetime.fromtimestamp(c.started_at):%-d.%-m.%Y} – {c.segments} lines</option>"
        for c in choices
    )
    disabled = " disabled" if blocked else ""
    style = (
        "text-white px-4 py-2 rounded transition disabled:bg-slate-600 disabled:cursor-not-allowed"
    )
    buttons = (
        f"<button type='submit' class='bg-blue-600 hover:bg-blue-500 {style}'{disabled}>"
        "Generate comic (8–10 pages)</button>"
        # htmx sends the clicked button's name/value with the form.
        f"<button type='submit' name='mode' value='test' "
        f"class='bg-slate-500 hover:bg-slate-400 {style}'{disabled}>Test run (1–2 pages)</button>"
    )
    reason = f"<span class='text-amber-400'>{escape(blocked)}</span>" if blocked else ""
    return (
        "<form hx-post='/api/v1/dashboard/generate' hx-target='#generate-result' "
        "class='space-y-4'>"
        "<select name='session_id' hx-get='/api/v1/dashboard/transcript' "
        "hx-trigger='load, change' hx-target='#transcript-preview' "
        "class='w-full bg-slate-700 text-white p-2 rounded'>"
        f"{options}</select>"
        f"<div class='flex flex-wrap items-center gap-4'>{buttons}{reason}"
        "<span id='generate-result'></span></div></form>"
        "<pre id='transcript-preview' class='mt-4 text-sm text-slate-300 whitespace-pre-wrap "
        "bg-slate-900 p-3 rounded'></pre>"
    )


@app.get("/api/v1/dashboard/transcript", response_class=HTMLResponse)
async def get_transcript_preview(session_id: str) -> str:
    if get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    lines = transcript(session_id).splitlines()
    more = f"\n… {len(lines) - PREVIEW_LINES} more lines" if len(lines) > PREVIEW_LINES else ""
    return escape("\n".join(lines[:PREVIEW_LINES]) + more)


async def _queue_comic(request: Request, session_id: str, test: bool) -> bool:
    """Queue a comic for a finished session. Returns False if it is already queued/running."""
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    if session.live:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Session is still recording; stop it first (/session stop)",
        )
    if lock_info() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A comic is already being rendered")
    if any_live():
        # Rendering stops STT, which would cut off the session that is recording.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A session is recording")
    # One job per session at a time: arq ignores a job id that is already queued or running.
    job = await request.app.state.queue.enqueue_job(
        "generate_comic", session_id, test, _job_id=f"comic:{session_id}"
    )
    return job is not None


@app.post("/api/v1/dashboard/generate", response_class=HTMLResponse)
async def generate_from_dashboard(request: Request) -> str:
    # htmx posts the form URL-encoded; parsed here to avoid a python-multipart dependency.
    form = parse_qs((await request.body()).decode())
    session_id = (form.get("session_id") or [""])[0]
    test = (form.get("mode") or [""])[0] == "test"
    try:
        queued = await _queue_comic(request, session_id, test)
    except HTTPException as exc:
        return f"<span class='text-red-400 font-bold'>{escape(str(exc.detail))}</span>"
    kind = "Test run (1–2 pages)" if test else "Comic (8–10 pages)"
    message = (
        f"{kind} queued – comic mode starts after the script is written."
        if queued
        else "Already queued or running."
    )
    return f"<span class='text-green-400 font-bold'>{message}</span>"


@app.post("/api/v1/comic/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_comic_generation(session_id: str, request: Request, test: bool = False):
    """Queue a comic; ?test=true makes a 1-2 page test run."""
    queued = await _queue_comic(request, session_id, test)
    return {
        "status": "accepted" if queued else "already_queued",
        "session_id": session_id,
        "test": test,
    }


# --- comic bible -----------------------------------------------------------------------------


def _bible_card(request: Request, message: str = "", error: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "bible.html", {"bible": bible.load(), "message": message, "error": error}
    )


@app.get("/api/v1/bible", response_class=HTMLResponse)
async def get_bible(request: Request):
    return _bible_card(request)


@app.post("/api/v1/bible/campaign", response_class=HTMLResponse)
async def save_campaign(
    request: Request,
    setting: str = Form(""),
    tone: str = Form(""),
    bubble_language: str = Form("Finnish"),
    style_positive: str = Form(""),
    style_negative: str = Form(""),
):
    b = bible.load()
    b.setting, b.tone = setting.strip(), tone.strip()
    b.bubble_language = bubble_language.strip() or "Finnish"
    b.style.positive, b.style.negative = style_positive.strip(), style_negative.strip()
    bible.save(b)
    return _bible_card(request, "Saved.")


@app.post("/api/v1/bible/characters", response_class=HTMLResponse)
async def add_character(request: Request, name: str = Form("")):
    if not name.strip():
        return _bible_card(request, "Name is required.", error=True)
    bible.add_character(name)
    return _bible_card(request, f"Added {name.strip()}.")


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
):
    with _known_character():
        bible.update_character(character_id, name, aliases.split(","), appearance)
    return _bible_card(request, "Saved.")


@app.post("/api/v1/bible/characters/{character_id}/delete", response_class=HTMLResponse)
async def delete_character(request: Request, character_id: str):
    with _known_character():
        bible.delete_character(character_id)
    return _bible_card(request, "Character deleted.")


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
                return _bible_card(request, f"{f.filename}: {exc}", error=True)
            stored += 1
    return _bible_card(request, f"Uploaded {stored} image(s).")


@app.get("/api/v1/bible/characters/{character_id}/images/{name}")
async def get_image(character_id: str, name: str):
    with _known_character():
        path = bible.image_path(character_id, name)
    return FileResponse(path, media_type="image/png")


@app.post(
    "/api/v1/bible/characters/{character_id}/images/{name}/delete", response_class=HTMLResponse
)
async def delete_image(request: Request, character_id: str, name: str):
    with _known_character():
        bible.delete_image(character_id, name)
    return _bible_card(request, "Image removed.")


@app.post("/api/v1/bible/characters/{character_id}/describe", response_class=HTMLResponse)
async def draft_appearance(request: Request, character_id: str, appearance: str = Form("")):
    """Let Gemini draft the appearance text from the reference images. Not saved until Save."""
    with _known_character():
        character = bible.load().character(character_id)
        images = [bible.image_path(character_id, n).read_bytes() for n in character.images]

    def fragment(text: str, note: str, error: bool = False):
        context = {"c": character, "appearance": text, "note": note, "error": error}
        return templates.TemplateResponse(request, "appearance.html", context)

    if not images:
        return fragment(appearance, "Upload reference images first.", error=True)
    try:
        draft = await run_in_threadpool(describe_character, character.name, images, appearance)
    except Exception as exc:  # Gemini errors vary; show them instead of a 500
        return fragment(appearance, f"Gemini failed: {exc}", error=True)
    return fragment(draft, "Draft from the images. Edit it, then press Save.")
