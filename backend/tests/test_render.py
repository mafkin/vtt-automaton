from app.render.foundry import render_foundry
from app.rules.models import RuleRef, Ruling


def ref(name, text, n=0):
    return RuleRef(
        entry_id=f"rules-{n}",
        category="rules",
        name=name,
        aon_url=f"https://2e.aonprd.com/Rules.aspx?ID={n}",
        source="Player Core pg. 1",
        text=text,
        quote=text[:20],
    )


def ruling(refs, interpretation="Tulkinta tähän.", raw_only=False):
    return Ruling(
        query="Kysymys?",
        raw=refs,
        interpretation=interpretation,
        confidence="medium",
        raw_only=raw_only,
    )


def test_foundry_shows_full_raw_then_ruling():
    html = render_foundry(ruling([ref("Trip", "Line one.\nLine two.\n\nSecond paragraph.")]))
    assert "<p>Line one.<br>Line two.</p><p>Second paragraph.</p>" in html
    assert html.index("Säännöt (RAW)") < html.index("Second paragraph.") < html.index("Tulkinta")
    assert "Player Core pg. 1" in html


def test_foundry_prints_long_entries_in_full_collapsed():
    text = "word " * 2000
    html = render_foundry(ruling([ref("Encounter Mode", text), ref("Prone", "Short.", 1)]))
    assert html.count("word") == 2000
    assert html.count("<details>") == 1
    assert "<header>" in html  # the short entry is shown open


def test_foundry_english_labels():
    html = render_foundry(ruling([ref("Trip", "x")]), language="English")
    assert "Rules as Written" in html and "Ruling" in html
