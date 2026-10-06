from app.bible import Bible, Character, Style
from app.comics import Balloon, Moment, ScriptPage, ScriptPanel
from app.llm import draw_prompt, events_prompt, lettering_problems, script_prompt


def campaign() -> Bible:
    return Bible(
        setting="Restov, southern Brevoy",
        tone="light-hearted",
        bubble_language="Finnish",
        style=Style(positive="ink comic on parchment", negative="photo"),
        characters=[
            Character(id="pentik", name="Pentik", appearance="great helm", images=["a.png"]),
            Character(id="rintaro", name="Rintaro", aliases=["Rin"], appearance="seal"),
        ],
    )


def page() -> ScriptPage:
    return ScriptPage(
        title="Ei-kuolettava kuulustelu",
        characters=["Pentik", "Rin"],
        panels=[
            ScriptPanel(
                visual="Pentik points his flail",
                balloons=[Balloon(speaker="Pentik", text="Ota tuo elävänä!")],
            ),
            ScriptPanel(
                visual="Rintaro strikes",
                balloons=[Balloon(speaker="Rintaro", text="Hups. Arvioin eväni voiman väärin.")],
            ),
        ],
    )


def test_events_prompt_has_campaign_and_transcript_and_asks_for_facts_only():
    p = events_prompt("Puhuja: Pentik potkaisee oven auki.", campaign())
    assert "Restov" in p and "Pentik potkaisee oven auki" in p
    assert "do not invent" in p.lower()


def test_script_prompt_has_moments_language_and_page_per_moment():
    moments = [
        Moment(title="Interrogation", summary="Rintaro kills the prisoner", characters=["Rintaro"])
    ]
    p = script_prompt("1. Fight in the garden", moments, campaign())
    assert "Interrogation" in p and "Rintaro kills the prisoner" in p and "Fight in the garden" in p
    assert "one page per moment" in p.lower() and "Finnish" in p


def test_draw_prompt_letters_exactly_without_captions():
    cast = campaign().match(page().characters)
    p = draw_prompt(page(), campaign(), cast, extra="Pentik holds his shield")
    assert '"Ei-kuolettava kuulustelu"' in p and "Ota tuo elävänä!" in p
    assert "no captions" in p.lower() and "ink comic on parchment" in p and "photo" in p
    assert "Reference image 1 is PENTIK" in p and "RINTARO" in p
    assert "Pentik holds his shield" in p


def test_lettering_that_matches_is_fine_despite_small_ocr_noise():
    read = ["OTA TUO ELÄVÄNÄ!", "Hups. Arvioin eväni voiman väärin"]
    assert lettering_problems(page(), read) == []


def test_lettering_problems_missing_twice_and_extra():
    read = ["Ota tuo elävänä!", "Ota tuo elävänä!", "Kirjaston ja Restov."]
    problems = lettering_problems(page(), read)
    assert "missing: Hups. Arvioin eväni voiman väärin." in problems
    assert "twice: Ota tuo elävänä!" in problems
    assert "extra: Kirjaston ja Restov." in problems


def test_a_typo_like_a_doubled_word_is_caught():
    read = ["Ota tuo elävänä!", "Hups. Arvioin evani eväni voiman väärin."]
    assert lettering_problems(page(), read) == ["missing: Hups. Arvioin eväni voiman väärin."]
