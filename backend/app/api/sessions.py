import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.auth import require_client
from app.config import Settings
from app.sessions.store import Session, SessionStore, StoredSegment, format_transcript

router = APIRouter(prefix="/api/v1/sessions", dependencies=[Depends(require_client)])


class StartSession(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    source: str = Field(default="discord", max_length=40)


def get_store(request: Request) -> SessionStore:
    return request.app.state.session_store


Store = Annotated[SessionStore, Depends(get_store)]


def _require(store: SessionStore, session_id: str) -> Session:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown session")
    return session


def comic_in_progress(settings: Settings) -> bool:
    """Is the comic worker holding the GPU (speech-to-text stopped)?"""
    lock = settings.sessions_db_path.parent / "comic.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except FileNotFoundError:
        return False
    return age < settings.comic_lock_max_age_seconds


@router.post("", status_code=status.HTTP_201_CREATED, response_model=Session)
async def start_session(body: StartSession, store: Store, request: Request) -> Session:
    if comic_in_progress(request.app.state.settings):
        # Clients match on this detail to tell the table why (Discord bot: comicInProgress).
        raise HTTPException(status.HTTP_409_CONFLICT, detail="comic_in_progress")
    return store.start(label=body.label, source=body.source)


@router.post("/{session_id}/stop", response_model=Session)
async def stop_session(session_id: str, store: Store) -> Session:
    _require(store, session_id)
    return store.stop(session_id)


@router.get("", response_model=list[Session])
async def list_sessions(store: Store) -> list[Session]:
    return store.recent()


@router.get("/{session_id}", response_model=Session)
async def get_session(session_id: str, store: Store) -> Session:
    return _require(store, session_id)


@router.get("/{session_id}/transcript", response_model=None)
async def get_transcript(
    session_id: str, store: Store, format: Literal["json", "text"] = "json"
) -> list[StoredSegment] | PlainTextResponse:
    session = _require(store, session_id)
    segments = store.transcript(session_id)
    if format == "text":
        return PlainTextResponse(format_transcript(session, segments))
    return segments
