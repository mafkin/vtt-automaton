"""Read-only access to the local rules database.

The database is produced by ``app.ingest.aon`` from Archives of Nethys. This module defines
the schema that ingest must produce (``SCHEMA``) and the retrieval queries the rules service uses.
"""

import json
import re
import sqlite3
from pathlib import Path

from app.rules.models import RuleEntry

# Bump when the schema or the text conversion changes; the importer rebuilds older DBs.
SCHEMA_VERSION = "3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id        TEXT PRIMARY KEY,          -- AoN document id, e.g. "action-2382"
    category  TEXT NOT NULL,             -- action, condition, spell, feat, trait, rule, ...
    name      TEXT NOT NULL,             -- official English name
    aon_url   TEXT NOT NULL,
    traits    TEXT NOT NULL DEFAULT '[]',-- JSON array of trait names
    text      TEXT NOT NULL,             -- verbatim rules text, plain text
    markdown  TEXT,                      -- original AoN markdown (links, layout)
    source    TEXT,                      -- book and page
    legacy    INTEGER NOT NULL DEFAULT 0 -- 1 = only in pre-Remaster books; hidden from rulings
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,              -- schema_version, source_index, imported_at, ...
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entries_name ON entries (name COLLATE NOCASE);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    name, traits, text,
    content='entries', content_rowid='rowid',
    tokenize='porter unicode61'
);
"""

# Column weights for bm25: matches in the name count far more than matches in body text.
_BM25_WEIGHTS = (10.0, 3.0, 1.0)
_FTS_SQL = """
SELECT e.* FROM entries_fts f JOIN entries e ON e.rowid = f.rowid
WHERE entries_fts MATCH ? AND e.legacy = 0
ORDER BY bm25(entries_fts, ?, ?, ?) LIMIT ?
"""
_TOKEN_RE = re.compile(r"[\w'-]+", re.UNICODE)


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild')")


def _fts_query(terms: list[str]) -> str:
    """Build an FTS5 OR-query. Each term becomes a quoted phrase, so input can't inject syntax."""
    phrases = []
    for term in terms:
        tokens = _TOKEN_RE.findall(term)
        if tokens:
            phrases.append('"' + " ".join(t.replace('"', "") for t in tokens) + '"')
    return " OR ".join(phrases)


def _row_to_entry(row: sqlite3.Row) -> RuleEntry:
    return RuleEntry(
        id=row["id"],
        category=row["category"],
        name=row["name"],
        aon_url=row["aon_url"],
        traits=json.loads(row["traits"] or "[]"),
        text=row["text"],
        source=row["source"],
    )


class RulesStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise FileNotFoundError(f"Rules database not found: {self._db_path}")
        conn = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, entry_id: str) -> RuleEntry | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        return _row_to_entry(row) if row else None

    def search(self, terms: list[str], limit: int = 8) -> list[RuleEntry]:
        """Exact name matches first, then full-text matches ranked by bm25. Skips legacy entries."""
        terms = [t.strip() for t in terms if t.strip()]
        if not terms:
            return []

        results: dict[str, RuleEntry] = {}
        with self._connect() as conn:
            # One lookup per term keeps exact matches in the order the terms were given.
            for term in terms:
                for row in conn.execute(
                    "SELECT * FROM entries WHERE name = ? COLLATE NOCASE AND legacy = 0", (term,)
                ):
                    results.setdefault(row["id"], _row_to_entry(row))

            query = _fts_query(terms)
            if query and len(results) < limit:
                rows = conn.execute(
                    _FTS_SQL,
                    (query, *_BM25_WEIGHTS, limit),
                )
                for row in rows:
                    results.setdefault(row["id"], _row_to_entry(row))

        return list(results.values())[:limit]
