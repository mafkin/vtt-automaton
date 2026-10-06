import sqlite3
import time

import pytest

from app.config import settings

# The backend's sessions schema (backend/app/sessions/store.py), as the comic service reads it.
SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, label TEXT, source TEXT NOT NULL,
                       started_at REAL NOT NULL, ended_at REAL);
CREATE TABLE segments (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                       speaker TEXT NOT NULL, speaker_id TEXT, character TEXT,
                       t_start REAL, t_end REAL NOT NULL, text TEXT NOT NULL);
"""


@pytest.fixture
def sessions_db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    now = time.time()
    conn.executemany(
        "INSERT INTO sessions (id, label, source, started_at, ended_at)"
        " VALUES (?, ?, 'discord', ?, ?)",
        [
            ("ended1", "Session <b>12</b>", now - 7200, now - 60),
            ("live1", "Tonight", now - 600, None),
        ],
    )
    conn.executemany(
        "INSERT INTO segments (session_id, speaker, character, t_start, t_end, text)"
        " VALUES ('ended1', ?, ?, ?, ?, ?)",
        [
            ("Aino", "Valeros", now - 7000, now - 6995, "Hyökkään!"),
            ("GM", None, now - 7100, now - 7090, "Örkit hyökkäävät."),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "sessions_db_path", str(path))
    return path


@pytest.fixture(autouse=True)
def bible_dir(tmp_path, monkeypatch):
    path = tmp_path / "comic"
    monkeypatch.setattr(settings, "bible_dir", str(path))
    return path
