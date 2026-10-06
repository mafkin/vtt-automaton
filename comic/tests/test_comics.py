import pytest

from app import comics
from app.comics import BudgetExceeded, Limits


def test_create_load_and_list():
    c = comics.create("tyrmia-e3", "Tyrmia ja Turpakarajia E3")
    assert c.status == "new" and c.tokens_used == 0
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
        token_budget_per_comic=50_000, max_auto_redraws_per_page=1
    )
    comics.save_limits(Limits(token_budget_per_comic=3000, max_auto_redraws_per_page=0))
    assert comics.load_limits().token_budget_per_comic == 3000


def test_every_call_is_charged_and_the_budget_stops_the_next_image():
    comics.save_limits(Limits(token_budget_per_comic=5000))
    c = comics.create("s1", "S1")
    comics.charge(c, 2000)
    comics.charge(c, None)  # a response without usage data costs nothing, not a crash
    assert comics.load(c.id).tokens_used == 2000
    comics.ensure_budget(c, 2500)  # 4500 <= 5000: allowed
    comics.charge(c, 2000)
    with pytest.raises(BudgetExceeded, match="4000 / 5000"):
        comics.ensure_budget(c, 2500)
