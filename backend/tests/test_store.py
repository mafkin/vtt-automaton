from app.rules.store import _fts_query


def test_exact_name_match_comes_first(store):
    results = store.search(["prone", "lying"])
    assert results[0].name == "Prone"


def test_full_text_search_finds_body_matches(store):
    names = {e.name for e in store.search(["Athletics"])}
    assert names == {"Trip"}


def test_empty_terms_return_nothing(store):
    assert store.search(["", "  "]) == []


def test_fts_query_neutralises_syntax():
    assert _fts_query(['Trip" OR *', "NEAR(a b)"]) == '"Trip OR" OR "NEAR a b"'


def test_get_by_id(store):
    assert store.get("Actions.aspx?ID=1").name == "Trip"
    assert store.get("missing") is None


def test_legacy_entries_are_never_returned(store):
    assert store.search(["Attack of Opportunity"]) == []
    assert all(e.id != "action-8" for e in store.search(["melee", "Strike", "prone"]))


def test_rule_categories_rank_ahead_of_items_and_hazards(tmp_path):
    import json
    import sqlite3

    from app.rules.store import RulesStore, create_schema, rebuild_fts

    path = tmp_path / "rank.db"
    conn = sqlite3.connect(path)
    create_schema(conn)
    rows = [
        # The hazard mentions "prone" most often, so plain bm25 would put it first.
        ("hazard-1", "hazard", "Slippery Floor", "prone prone prone prone. You fall prone."),
        ("equipment-1", "equipment", "Trip Snare", "The target falls prone."),
        ("condition-1", "condition", "Off-Guard", "You are off-guard, for example while prone."),
    ]
    conn.executemany(
        "INSERT INTO entries (id, category, name, aon_url, traits, text)"
        " VALUES (?, ?, ?, '', ?, ?)",
        [(i, c, n, json.dumps([]), t) for i, c, n, t in rows],
    )
    rebuild_fts(conn)
    conn.commit()
    conn.close()

    ids = [e.id for e in RulesStore(path).search(["prone"], limit=3)]
    assert ids[0] == "condition-1"
    assert set(ids[1:]) == {"hazard-1", "equipment-1"}
