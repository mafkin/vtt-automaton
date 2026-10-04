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
