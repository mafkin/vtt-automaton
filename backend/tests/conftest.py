import json
import sqlite3
from pathlib import Path

import pytest

from app.rules.store import RulesStore, create_schema, rebuild_fts

# Synthetic fixture entries. These are NOT the official rules text.
FIXTURE_ENTRIES = [
    {
        "id": "Actions.aspx?ID=1",
        "category": "action",
        "name": "Trip",
        "aon_url": "https://2e.aonprd.com/Actions.aspx?ID=1",
        "traits": ["attack"],
        "text": "Fixture: Attempt an Athletics check against the target's Reflex DC. "
        "On a success, the target falls and lands prone.",
        "source": "Fixture",
    },
    {
        "id": "Conditions.aspx?ID=2",
        "category": "condition",
        "name": "Prone",
        "aon_url": "https://2e.aonprd.com/Conditions.aspx?ID=2",
        "traits": [],
        "text": "Fixture: You are lying on the ground. You are off-guard and take a penalty "
        "to attack rolls.",
        "source": "Fixture",
    },
    {
        "id": "Conditions.aspx?ID=3",
        "category": "condition",
        "name": "Off-Guard",
        "aon_url": "https://2e.aonprd.com/Conditions.aspx?ID=3",
        "traits": [],
        "text": "Fixture: You take a circumstance penalty to your AC.",
        "source": "Fixture",
    },
    {
        "id": "action-8",
        "category": "action",
        "name": "Attack of Opportunity",
        "aon_url": "https://2e.aonprd.com/Actions.aspx?ID=8",
        "traits": [],
        "text": "Fixture: legacy reaction. Make a melee Strike against the target who fell prone.",
        "source": "Fixture legacy",
        "legacy": 1,
    },
]


@pytest.fixture
def rules_db(tmp_path: Path) -> Path:
    path = tmp_path / "rules.db"
    conn = sqlite3.connect(path)
    create_schema(conn)
    conn.executemany(
        "INSERT INTO entries (id, category, name, aon_url, traits, text, source, legacy) "
        "VALUES (:id, :category, :name, :aon_url, :traits, :text, :source, :legacy)",
        [{"legacy": 0, **e, "traits": json.dumps(e["traits"])} for e in FIXTURE_ENTRIES],
    )
    rebuild_fts(conn)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def store(rules_db: Path) -> RulesStore:
    return RulesStore(rules_db)
