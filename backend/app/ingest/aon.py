"""Import rules from Archives of Nethys into the local rules database.

AoN serves its site search from a public Elasticsearch index. We page through it with
``search_after``, keep the current (Remaster) version of every entry, convert the markdown to
plain text and write a fresh SQLite file that atomically replaces the old one. Each import
records the AoN index name, so an unchanged index is skipped.

Usage::

    uv run python -m app.ingest.aon [--db PATH] [--force]
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.ingest.aon_text import markdown_to_text
from app.rules.store import create_schema, rebuild_fts

log = logging.getLogger(__name__)

AON_SEARCH_URL = "https://elasticsearch.aonprd.com/aon/_search"
AON_SITE_URL = "https://2e.aonprd.com"
USER_AGENT = "vtt-automaton rules import (+https://github.com/mafkin/vtt-automaton)"

# Navigation and bibliography pages, not rules.
SKIPPED_CATEGORIES = frozenset({"category-page", "source"})

_SOURCE_FIELDS = [
    "id", "category", "name", "url", "markdown", "trait", "primary_source_raw",
    "remaster_id", "exclude_from_search",
]  # fmt: skip


_INSERT_SQL = """
INSERT OR REPLACE INTO entries (id, category, name, aon_url, traits, text, markdown, source)
VALUES (:id, :category, :name, :aon_url, :traits, :text, :markdown, :source)
"""


@dataclass
class ImportResult:
    skipped: bool
    source_index: str
    entry_count: int = 0


class AonClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        search_url: str = AON_SEARCH_URL,
        page_size: int = 500,
        page_delay: float = 0.5,
    ) -> None:
        self._http = http or httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
        self._url = search_url
        self._page_size = page_size
        self._page_delay = page_delay

    def _search(self, body: dict) -> dict:
        response = self._http.post(self._url, json=body)
        response.raise_for_status()
        return response.json()

    def current_index(self) -> str:
        """Name of the concrete index behind the ``aon`` alias; changes on every AoN rebuild."""
        hits = self._search({"size": 1, "_source": False})["hits"]["hits"]
        return hits[0]["_index"]

    def iter_documents(self) -> Iterator[dict]:
        search_after = None
        while True:
            body: dict = {
                "size": self._page_size,
                "_source": _SOURCE_FIELDS,
                "sort": [{"id.keyword": "asc"}],
                "query": {"match_all": {}},
            }
            if search_after is not None:
                body["search_after"] = search_after
            hits = self._search(body)["hits"]["hits"]
            if not hits:
                return
            for hit in hits:
                yield hit["_source"]
            search_after = hits[-1]["sort"]
            if self._page_delay:
                time.sleep(self._page_delay)


def to_row(doc: dict) -> dict | None:
    """Map an AoN document to an ``entries`` row, or None if it should not be imported."""
    if doc.get("remaster_id"):  # legacy entry superseded by a Remaster version
        return None
    if doc.get("exclude_from_search"):  # sub-entries such as item activations
        return None
    if doc.get("category") in SKIPPED_CATEGORIES:
        return None
    markdown = doc.get("markdown") or ""
    text = markdown_to_text(markdown)
    if not doc.get("name") or not text:
        return None
    url = doc.get("url") or ""
    return {
        "id": doc["id"],
        "category": doc["category"],
        "name": doc["name"],
        "aon_url": AON_SITE_URL + url if url.startswith("/") else url,
        "traits": json.dumps(doc.get("trait") or []),
        "text": text,
        "markdown": markdown,
        "source": doc.get("primary_source_raw"),
    }


def read_meta(db_path: Path, key: str) -> str | None:
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def import_rules(
    db_path: Path, client: AonClient | None = None, force: bool = False
) -> ImportResult:
    client = client or AonClient()
    source_index = client.current_index()
    if not force and read_meta(db_path, "source_index") == source_index:
        log.info("Rules DB is up to date with %s", source_index)
        return ImportResult(skipped=True, source_index=source_index)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    conn = sqlite3.connect(tmp_path)
    try:
        create_schema(conn)
        count = 0
        batch: list[dict] = []
        for doc in client.iter_documents():
            row = to_row(doc)
            if row is None:
                continue
            batch.append(row)
            if len(batch) >= 1000:
                count += _insert(conn, batch)
                batch.clear()
                log.info("Imported %d entries", count)
        count += _insert(conn, batch)
        if count == 0:
            raise RuntimeError("AoN returned no importable entries; keeping the existing DB")
        rebuild_fts(conn)
        conn.executemany(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            [
                ("source_index", source_index),
                ("imported_at", datetime.now(UTC).isoformat(timespec="seconds")),
                ("entry_count", str(count)),
            ],
        )
        conn.commit()
    except BaseException:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    conn.close()

    # Atomic swap: requests already holding the old file finish against it, new ones see the new.
    os.replace(tmp_path, db_path)
    log.info("Rules DB updated: %d entries from %s", count, source_index)
    return ImportResult(skipped=False, source_index=source_index, entry_count=count)


def _insert(conn: sqlite3.Connection, rows: list[dict]) -> int:
    # INSERT OR REPLACE: AoN ids are unique, but don't let one bad duplicate abort the import.
    conn.executemany(_INSERT_SQL, rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=None, help="defaults to VTT_RULES_DB_PATH")
    parser.add_argument("--force", action="store_true", help="re-import even if unchanged")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = args.db or get_settings().rules_db_path
    result = import_rules(db_path, force=args.force)
    print(json.dumps(result.__dict__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
