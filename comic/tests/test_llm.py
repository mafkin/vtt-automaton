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


def test_the_gemini_client_stays_alive_during_the_call(monkeypatch):
    # The real genai.Client closes its connection when garbage-collected; a call chained on a
    # temporary client ("_client().models.generate_content(...)") failed with "client has
    # been closed" on the server.
    from app import llm

    class Models:
        def __init__(self, owner):
            self.owner = owner

        def generate_content(self, **kwargs):
            assert not self.owner.state["closed"], "client was closed before the call"

            class Response:
                text = '{"events": "e", "moments": []}'
                usage_metadata = None

            return Response()

    class FakeClient:
        def __init__(self, api_key):
            self.state = {"closed": False}
            self.models = Models(self)
            # The Models only holds the state, not the client (as in the real SDK).
            self.models.owner = type("Ref", (), {"state": self.state})()

        def __del__(self):
            self.state["closed"] = True

    monkeypatch.setattr(llm.genai, "Client", FakeClient)
    result, tokens = llm.extract_events("t", campaign())
    assert result.events == "e" and tokens == 0


# --- consistency: traits, page look, style anchor, character sheets ---------------------------

from app.llm import describe_prompt, sheet_prompt  # noqa: E402


def test_draw_prompt_lists_must_have_traits_and_page_look():
    bible = campaign()
    bible.characters[0].traits = ["great helm", "blue shield with three gold towers"]
    bible.style.page_look = "white balloons, black borders"
    p = draw_prompt(page(), bible, bible.match(page().characters))
    assert "PENTIK must have: great helm; blue shield with three gold towers" in p
    assert "white balloons, black borders" in p
    assert "STYLE REFERENCE" not in p


def test_draw_prompt_with_anchor_names_it_as_the_last_image():
    bible = campaign()
    cast = bible.match(page().characters)
    p = draw_prompt(page(), bible, cast, anchor=True)
    assert f"image {len(cast) + 1} is a STYLE REFERENCE page" in p
    assert "don't copy its characters" in p.lower()


def test_sheet_prompt_asks_for_views_in_the_comic_style_without_text():
    bible = campaign()
    rintaro = bible.characters[1]
    rintaro.traits = ["spotted grey seal", "red headband"]
    p = sheet_prompt(rintaro, bible)
    assert "Rintaro" in p and "spotted grey seal; red headband" in p and "seal" in p
    assert "ink comic on parchment" in p
    assert "front" in p and "side" in p and "no text" in p.lower()


def test_description_draft_asks_for_traits_too():
    p = describe_prompt("Rintaro", "")
    assert "traits" in p and "pose" in p
