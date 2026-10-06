from fastapi import FastAPI, BackgroundTasks, status
from pydantic import BaseModel
from app.config import settings
from arq import create_pool
from arq.connections import RedisSettings

app = FastAPI(title="VTT Comic API")
redis_pool = None

from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi.responses import HTMLResponse
import httpx
import sqlite3

templates = Jinja2Templates(directory="app/templates")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/api/v1/dashboard/containers")
async def get_containers():
    # Hit the docker proxy
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.docker_proxy_url}/containers/json")
            r.raise_for_status()
            containers = r.json()
            html = "<ul class='space-y-2'>"
            for c in containers:
                name = c['Names'][0].lstrip('/')
                state = c['State']
                color = "text-green-400" if state == "running" else "text-red-400"
                html += f"<li class='flex justify-between border-b border-slate-700 pb-2'><span>{name}</span><span class='{color}'>{state}</span></li>"
            html += "</ul>"
            return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p class='text-red-500'>Error loading containers: {e}</p>")

@app.get("/api/v1/dashboard/sessions")
async def get_sessions():
    try:
        conn = sqlite3.connect("/data/sessions.db")
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT id, label, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 5").fetchall()
        conn.close()
        
        html = "<ul class='space-y-4'>"
        for r in rows:
            label = r['label'] or "Unnamed Session"
            status = "Live" if not r['ended_at'] else "Ended"
            status_color = "text-amber-400" if not r['ended_at'] else "text-slate-400"
            html += f"<li class='bg-slate-700 p-4 rounded flex items-center justify-between'>"
            html += f"<div><div class='font-bold text-white'>{label}</div><div class='text-sm text-slate-400'>ID: {r['id']}</div></div>"
            html += f"<div class='flex items-center gap-4'>"
            html += f"<span class='{status_color}'>{status}</span>"
            html += f"<button hx-post='/api/v1/comic/{r['id']}' hx-swap='outerHTML' class='bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded transition'>Generate Comic</button>"
            html += f"</div></li>"
        html += "</ul>"
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<p class='text-red-500'>Error loading sessions: {e}</p>")


@app.on_event("startup")
async def startup():
    global redis_pool
    # redis://redis:6379/0 -> parse to RedisSettings
    # We can just use the default RedisSettings for simplicity if redis is on localhost.
    # But here we parse it:
    redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))

@app.post("/api/v1/comic/{session_id}", status_code=status.HTTP_202_ACCEPTED)
async def trigger_comic_generation(session_id: str, request: Request):
    await redis_pool.enqueue_job("generate_comic", session_id)
    if request.headers.get("hx-request"):
        return HTMLResponse("<span class='text-green-400 font-bold'>Queued! Check worker logs.</span>")
    return {"status": "accepted", "session_id": session_id}
