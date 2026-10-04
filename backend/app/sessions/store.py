"""Game sessions and their transcripts, stored in a small SQLite database.

Kept separate from the rules database, which is rebuilt from Archives of Nethys and swapped out
wholesale; this one holds the table's own data and is only ever appended to.
"""

import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    label       TEXT,
    source      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL
);
CREATE TABLE IF NOT EXISTS segments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id),
    speaker     TEXT NOT NULL,
    speaker_id  TEXT,
    character   TEXT,
    t_start     REAL,
    t_end       REAL NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS segments_session ON segments (session_id, t_end);
"""


class Session(BaseModel):
    id: str
    label: str | None
    source: str
    started_at: float
    ended_at: float | None
    segment_count: int = 0


class StoredSegment(BaseModel):
    speaker: str
    speaker_id: str | None
    character: str | None
    t_start: float | None
    t_end: float
    text: str


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def start(self, label: str | None = None, source: str = "discord") -> Session:
        session = Session(
            id=uuid.uuid4().hex[:12],
            label=label,
            source=source,
            started_at=time.time(),
            ended_at=None,
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO sessions (id, label, source, started_at) VALUES (?, ?, ?, ?)",
                (session.id, session.label, session.source, session.started_at),
            )
        return session

    def stop(self, session_id: str) -> Session | None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (time.time(), session_id),
            )
        return self.get(session_id)

    def get(self, session_id: str) -> Session | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM segments g WHERE g.session_id = s.id) AS n "
                "FROM sessions s WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return _session(row) if row else None

    def recent(self, limit: int = 20) -> list[Session]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM segments g WHERE g.session_id = s.id) AS n "
                "FROM sessions s ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_session(r) for r in rows]

    def add_segment(self, session_id: str, segment: StoredSegment) -> bool:
        """Store a segment. Late segments after stop are kept; unknown sessions are refused."""
        with closing(self._connect()) as conn, conn:
            if not conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
                return False
            conn.execute(
                "INSERT INTO segments (session_id, speaker, speaker_id, character, t_start, t_end,"
                " text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    segment.speaker,
                    segment.speaker_id,
                    segment.character,
                    segment.t_start,
                    segment.t_end,
                    segment.text,
                ),
            )
        return True

    def transcript(self, session_id: str) -> list[StoredSegment]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT speaker, speaker_id, character, t_start, t_end, text FROM segments "
                "WHERE session_id = ? ORDER BY COALESCE(t_start, t_end), id",
                (session_id,),
            ).fetchall()
        return [StoredSegment(**dict(r)) for r in rows]


def _session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        label=row["label"],
        source=row["source"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        segment_count=row["n"],
    )


def format_transcript(session: Session, segments: list[StoredSegment]) -> str:
    """Plain-text transcript: one "[mm:ss] Speaker (Character): text" line per segment."""
    lines = [f"# {session.label or 'Session'} ({session.id})"]
    for s in segments:
        offset = max(0, int((s.t_start or s.t_end) - session.started_at))
        who = f"{s.speaker} ({s.character})" if s.character else s.speaker
        lines.append(
            f"[{offset // 3600:d}:{offset // 60 % 60:02d}:{offset % 60:02d}] {who}: {s.text}"
        )
    return "\n".join(lines) + "\n"
