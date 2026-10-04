import json
import sqlite3

import httpx
import pytest

from app.ingest.aon import AonClient, import_rules, read_meta, to_row
from app.ingest.aon_text import markdown_to_text
from app.rules.store import RulesStore

# Synthetic document using AoN's markup; not official rules text.
TRIP_MARKDOWN = """<title level="1" right="Action">[Trip](/Actions.aspx?ID=2382) \
<actions string="Single Action" /></title>

<traits>
<trait label="Attack" url="/Traits.aspx?ID=15" />
</traits>

<column gap="tiny">

**Source** [Player Core](/Sources.aspx?ID=216) pg. 236

**Requirements** You have a free hand.

</column>

---

Roll [Athletics](/Skills.aspx?ID=3) against the target&#39;s Reflex DC.

**Success** The target lands [prone](/Conditions.aspx?ID=31).<br />
**Critical Failure** You land prone."""


def doc(id_, name="Trip", category="action", **extra):
    return {
        "id": id_,
        "category": category,
        "name": name,
        "url": f"/Actions.aspx?ID={id_}",
        "markdown": TRIP_MARKDOWN,
        "trait": ["Attack"],
        "primary_source_raw": "Player Core pg. 236",
        **extra,
    }


def test_markdown_to_text():
    assert markdown_to_text(TRIP_MARKDOWN) == (
        "Requirements You have a free hand.\n\n"
        "Roll Athletics against the target's Reflex DC.\n\n"
        "Success The target lands prone.\n"
        "Critical Failure You land prone."
    )


def test_markdown_keeps_action_glyphs_in_body():
    assert markdown_to_text('Use <actions string="Reaction" /> Shield Block.') == (
        "Use [reaction] Shield Block."
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"remaster_id": ["action-2382"]},
        {"exclude_from_search": True},
        {"category": "source"},
        {"markdown": ""},
    ],
)
def test_to_row_skips(extra):
    assert to_row(doc("action-40", **extra)) is None


def test_legacy_flag_requires_all_sources_to_be_legacy():
    assert to_row(doc("action-8", source=["Core Rulebook"]))["legacy"] == 1
    assert to_row(doc("x-1", source=["Core Rulebook", "Player Core"]))["legacy"] == 0
    assert to_row(doc("x-2", source=["Secrets of Magic"]))["legacy"] == 0
    assert to_row(doc("x-3"))["legacy"] == 0


def test_to_row_maps_fields():
    row = to_row(doc("action-2382", legacy_id=["action-40"]))
    assert row["aon_url"] == "https://2e.aonprd.com/Actions.aspx?ID=action-2382"
    assert json.loads(row["traits"]) == ["Attack"]
    assert row["text"].startswith("Requirements")
    assert row["source"] == "Player Core pg. 236"


class FakeAon:
    """Mimics the AoN Elasticsearch endpoint: alias lookup plus search_after paging."""

    def __init__(self, docs, index="aon-1"):
        self.docs = sorted(docs, key=lambda d: d["id"])
        self.index = index
        self.page_requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("_source") is False:
            return httpx.Response(200, json={"hits": {"hits": [{"_index": self.index}]}})
        self.page_requests += 1
        after = body.get("search_after", [""])[0]
        page = [d for d in self.docs if d["id"] > after][: body["size"]]
        hits = [{"_index": self.index, "_source": d, "sort": [d["id"]]} for d in page]
        return httpx.Response(200, json={"hits": {"hits": hits}})

    def client(self, page_size=2):
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return AonClient(http=http, search_url="https://aon.test/_search",
                         page_size=page_size, page_delay=0)  # fmt: skip


def make_docs():
    return [
        doc("action-2382"),
        doc("action-40", remaster_id=["action-2382"]),
        doc("condition-31", name="Prone", category="condition"),
        doc("spell-1", name="Force Barrage", category="spell"),
        doc("source-1", name="Player Core", category="source"),
    ]


def test_import_builds_searchable_db(tmp_path):
    db = tmp_path / "rules.db"
    aon = FakeAon(make_docs())

    result = import_rules(db, client=aon.client())

    assert not result.skipped
    assert result.entry_count == 3
    assert aon.page_requests == 4  # 5 docs / page size 2, plus the empty final page
    assert read_meta(db, "source_index") == "aon-1"
    store = RulesStore(db)
    assert store.search(["Trip"])[0].id == "action-2382"
    assert {e.name for e in store.search(["Athletics"])} == {"Trip", "Prone", "Force Barrage"}


def test_import_skips_unchanged_index_and_reimports_new_one(tmp_path):
    db = tmp_path / "rules.db"
    import_rules(db, client=FakeAon(make_docs()).client())

    unchanged = FakeAon(make_docs())
    assert import_rules(db, client=unchanged.client()).skipped
    assert unchanged.page_requests == 0

    rebuilt = FakeAon(make_docs()[:1], index="aon-2")
    result = import_rules(db, client=rebuilt.client())
    assert result.entry_count == 1
    assert read_meta(db, "source_index") == "aon-2"


def test_schema_change_forces_reimport(tmp_path):
    db = tmp_path / "rules.db"
    import_rules(db, client=FakeAon(make_docs()).client())
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")

    assert not import_rules(db, client=FakeAon(make_docs()).client()).skipped


def test_failed_import_keeps_existing_db(tmp_path):
    db = tmp_path / "rules.db"
    import_rules(db, client=FakeAon(make_docs()).client())

    def broken(request):
        if json.loads(request.content).get("_source") is False:
            return httpx.Response(200, json={"hits": {"hits": [{"_index": "aon-3"}]}})
        return httpx.Response(503)

    client = AonClient(http=httpx.Client(transport=httpx.MockTransport(broken)),
                       search_url="https://aon.test/_search", page_delay=0)  # fmt: skip
    with pytest.raises(httpx.HTTPStatusError):
        import_rules(db, client=client)

    assert read_meta(db, "source_index") == "aon-1"
    assert not (tmp_path / "rules.db.tmp").exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 3
