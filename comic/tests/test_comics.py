import pytest

from app import comics
from app.comics import BudgetExceeded, Limits


def test_create_load_and_list():
    c = comics.create("tyrmia-e3", "Tyrmia ja Turpakarajia E3")
    assert c.status == "new" and c.image_tokens == 0 and c.text_tokens == 0
    assert comics.load(c.id).label == "Tyrmia ja Turpakarajia E3"
    assert [x.id for x in comics.list_comics()] == [c.id]


def test_unknown_or_unsafe_ids_are_key_errors():
    for bad in ["nope", "../bible", ""]:
        with pytest.raises(KeyError):
            comics.load(bad)


def test_saved_files_are_readable_on_the_host(bible_dir):
    c = comics.create("s1", "S1")
    path = bible_dir / "comics" / c.id / "comic.json"
    assert path.stat().st_mode & 0o777 == 0o644


def test_page_versions_are_kept(bible_dir):
    c = comics.create("s1", "S1")
    c.script = [comics.ScriptPage(title="T", panels=[comics.ScriptPanel(visual="v")])]
    first = comics.add_page_version(c, 0, b"png-1")
    second = comics.add_page_version(c, 0, b"png-2")
    assert (first, second) == ("page_1_v1.png", "page_1_v2.png")
    assert c.pages[0].versions == [first, second] and c.pages[0].current == second
    assert comics.page_path(c.id, first).read_bytes() == b"png-1"
    with pytest.raises(KeyError):
        comics.page_path(c.id, "../comic.json")


def test_limits_round_trip_with_defaults():
    assert comics.load_limits() == Limits(
        token_budget_per_comic=80_000, max_auto_redraws_per_page=1
    )
    comics.save_limits(Limits(token_budget_per_comic=3000, max_auto_redraws_per_page=0))
    assert comics.load_limits().token_budget_per_comic == 3000


def test_drawing_is_budgeted_and_text_calls_are_only_counted():
    comics.save_limits(Limits(token_budget_per_comic=5000))
    c = comics.create("s1", "S1")
    comics.charge(c, 30_000, "text")  # reading a long transcript: counted, not budgeted
    comics.charge(c, 2000, "image")
    comics.charge(c, None, "image")  # a response without usage data costs nothing
    c = comics.load(c.id)
    assert (c.text_tokens, c.image_tokens) == (30_000, 2000)
    comics.ensure_budget(c, 2500)  # 4500 <= 5000: allowed
    comics.charge(c, 2000, "image")
    with pytest.raises(BudgetExceeded, match="4000 / 5000"):
        comics.ensure_budget(c, 2500)


def test_rounds_group_versions_for_side_by_side_comparison():
    c = comics.create("s1", "S1")
    page = comics.ScriptPage(title="T", panels=[comics.ScriptPanel(visual="v")])
    c.script = [page, page]
    info = comics.VersionInfo
    # Round 1 draws both pages (page 1 needed an automatic redraw); round 2 redraws page 2.
    comics.add_page_version(c, 0, b"a", info(round="r1", refs={"Pentik": "image"}))
    comics.add_page_version(c, 0, b"b", info(round="r1", refs={"Pentik": "image"}))
    comics.add_page_version(c, 1, b"c", info(round="r1", refs={"Pentik": "image"}))
    comics.add_page_version(
        c, 1, b"d", info(round="r2", refs={"Pentik": "sheet_1.png"}, anchor=True)
    )
    rounds = comics.rounds(comics.load(c.id))
    assert [r.id for r in rounds] == ["r1", "r2"]
    assert rounds[0].pages == ["page_1_v2.png", "page_2_v1.png"]  # the last try of each page
    assert rounds[1].pages == [None, "page_2_v2.png"]
    assert rounds[1].label == "Pentik: sheet_1.png · style anchor"
    assert rounds[0].label == "Pentik: image"
