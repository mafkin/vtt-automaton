import contextlib
import sqlite3
from collections.abc import AsyncIterator
from html import escape

import httpx
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.sessions import get_session, recent_sessions


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Tests put a fake queue here before starting the app.
    if getattr(app.state, "queue", None) is None:
        app.state.queue = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    yield


app = FastAPI(title="VTT Comic API", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


@app.get("/api/v1/dashboard/containers", response_class=HTMLResponse)
async def get_containers() -> str:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.docker_proxy_url}/containers/json", timeout=10.0)
            r.raise_for_status()
            containers = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"<p class='text-red-500'>Error loading containers: {escape(str(exc))}</p>"
    items = []
    for c in containers:
        name = escape(c["Names"][0].lstrip("/"))
        state = escape(c["State"])
        color = "text-green-400" if c["State"] == "running" else "text-red-400"
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
        # A live session can't get a comic: rendering needs the GPU the transcription is using.
        action = (
            "<span class='text-amber-400'>Live (stop it first)</span>"
            if s.live
            else f"<button hx-post='/api/v1/comic/{escape(s.id)}' hx-swap='outerHTML' "
            "class='bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded transition'>"
            "Generate Comic</button>"
        )
        items.append(
            "<li class='bg-slate-700 p-4 rounded flex items-center justify-between'>"
            f"<div><div class='font-bold text-white'>{escape(s.label or 'Unnamed Session')}</div>"
            f"<div class='text-sm text-slate-400'>ID: {escape(s.id)}</div></div>"
            f"<div class='flex items-center gap-4'>{action}</div></li>"
        )
    return "<ul class='space-y-4'>" + "".join(items) + "</ul>"


@app.post("/api/v1/comic/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_comic_generation(session_id: str, request: Request):
    session = get_session(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    if session.live:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Session is still recording; stop it first (/session stop)",
        )
    # One job per session at a time: arq ignores a job id that is already queued or running.
    job = await request.app.state.queue.enqueue_job(
        "generate_comic", session_id, _job_id=f"comic:{session_id}"
    )
    if request.headers.get("hx-request"):
        message = "Queued! Check worker logs." if job else "Already queued or running."
        return HTMLResponse(f"<span class='text-green-400 font-bold'>{message}</span>")
    return {"status": "accepted" if job else "already_queued", "session_id": session_id}
