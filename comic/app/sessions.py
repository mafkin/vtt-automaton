"""Read-only access to the backend's sessions database (sessions and transcripts)."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class SessionInfo:
    id: str
    label: str | None
    started_at: float
    ended_at: float | None

    @property
    def live(self) -> bool:
        return self.ended_at is None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{settings.sessions_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_session(session_id: str) -> SessionInfo | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT id, label, started_at, ended_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return SessionInfo(**dict(row)) if row else None


def recent_sessions(limit: int = 5) -> list[SessionInfo]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, label, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [SessionInfo(**dict(r)) for r in rows]


@dataclass(frozen=True)
class TranscriptChoice:
    id: str
    label: str | None
    started_at: float
    segments: int


def ended_sessions_with_transcripts() -> list[TranscriptChoice]:
    """Finished sessions that have something to make a comic of, newest first."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT s.id, s.label, s.started_at, COUNT(g.id) AS segments FROM sessions s "
            "JOIN segments g ON g.session_id = s.id WHERE s.ended_at IS NOT NULL "
            "GROUP BY s.id ORDER BY s.started_at DESC"
        ).fetchall()
    return [TranscriptChoice(**dict(r)) for r in rows]


def character_tags(session_id: str) -> list[tuple[str, int]]:
    """The characters players spoke as (speaker tags from /link), with their line counts."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT character, COUNT(*) AS n FROM segments WHERE session_id = ? "
            "AND character IS NOT NULL AND character != '' GROUP BY character ORDER BY n DESC",
            (session_id,),
        ).fetchall()
    return [(r["character"], r["n"]) for r in rows]


def transcript(session_id: str) -> str:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT speaker, character, text FROM segments WHERE session_id = ? "
            "ORDER BY COALESCE(t_start, t_end), id",
            (session_id,),
        ).fetchall()
    return "\n".join(
        f"{r['speaker']} ({r['character']}): {r['text']}"
        if r["character"]
        else f"{r['speaker']}: {r['text']}"
        for r in rows
    )
