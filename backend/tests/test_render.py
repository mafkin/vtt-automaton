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


def test_foundry_partial_entry_links_to_full_rule():
    partial = ref("Encounter Mode", "Only the cited passage.").model_copy(update={"partial": True})
    html = render_foundry(ruling([partial, ref("Prone", "Short.", 1)]))
    assert html.count("Koko sääntö Archives of Nethysissä") == 1
    assert "<details>" not in html


def test_foundry_english_labels():
    html = render_foundry(ruling([ref("Trip", "x")]), language="English")
    assert "Rules as Written" in html and "Ruling" in html
