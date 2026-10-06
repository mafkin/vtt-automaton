import io

from PIL import Image

from app.bible import Bible, Character, Style
from app.comics import Balloon, Moment, ScriptPage, ScriptPanel
from app.llm import bible_context, draw_prompt, events_prompt, lettering_problems, script_prompt


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


def labelled(bible, *labels_per_character):
    """The cast as the pipeline passes it: (character, [reference labels])."""
    chars = bible.match(page().characters)
    return [(c, list(labels)) for c, labels in zip(chars, labels_per_character, strict=True)]


def test_draw_prompt_letters_exactly_without_captions():
    cast = labelled(campaign(), ["reference image"], ["reference image"])
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
        def __init__(self, api_key, http_options=None):
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
    bible.characters[0].never = ["a tabard", "a cross on the helm"]
    p = draw_prompt(page(), bible, labelled(bible, ["full-body sheet"], ["reference image"]))
    assert "PENTIK must have: great helm; blue shield with three gold towers" in p
    assert "PENTIK never: a tabard; a cross on the helm" in p
    # The character rules outrank the panel descriptions.
    authority = p[p.index("Order of authority") :]
    assert authority.index("character rules") < authority.index("panel descriptions")
    assert "white balloons, black borders" in p
    assert "STYLE REFERENCE" not in p


def test_reference_images_are_numbered_per_label_then_anchor_then_previous_page():
    bible = campaign()
    cast = labelled(bible, ["full-body sheet", "close-ups of details"], ["reference image"])
    p = draw_prompt(page(), bible, cast, anchor=True, previous=True)
    assert "Reference image 1 is PENTIK (full-body sheet)" in p
    assert "Reference image 2 is PENTIK (close-ups of details)" in p
    assert "Reference image 3 is RINTARO (reference image)" in p
    assert "Reference image 4 is a STYLE REFERENCE page" in p
    assert "Reference image 5 is the PREVIOUS PAGE" in p
    assert "don't copy its characters" in p.lower()


def test_without_anchor_or_previous_page_neither_is_mentioned():
    bible = campaign()
    p = draw_prompt(page(), bible, labelled(bible, ["reference image"], ["reference image"]))
    assert "STYLE REFERENCE" not in p and "PREVIOUS PAGE" not in p


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


def test_the_client_retries_overload_errors(monkeypatch):
    # A "503 UNAVAILABLE: high demand" from Gemini failed a whole drawing round on the server.
    from app import llm

    seen = {}

    class FakeClient:
        def __init__(self, api_key, http_options=None):
            seen["options"] = http_options

    monkeypatch.setattr(llm.genai, "Client", FakeClient)
    llm._client()
    retry = seen["options"].retry_options
    assert retry.attempts >= 3 and {429, 503} <= set(retry.http_status_codes)


from app.llm import detail_sheet_prompt, inspect_prompt  # noqa: E402


def test_detail_sheet_prompt_asks_for_close_ups_of_the_must_haves():
    bible = campaign()
    pentik = bible.characters[0]
    pentik.traits = ["flat-topped great helm", "blue shield with three gold towers"]
    pentik.never = ["a tabard"]
    p = detail_sheet_prompt(pentik, bible)
    assert "close-up" in p.lower() and "flat-topped great helm" in p and "a tabard" in p
    assert "first image is the approved full-body sheet" in p.lower()
    assert "no text" in p.lower()


def test_inspection_asks_for_lettering_and_only_clear_look_problems():
    bible = campaign()
    bible.characters[0].traits = ["blue shield with three gold towers"]
    bible.characters[0].never = ["a tabard"]
    p = inspect_prompt(bible.match(["Pentik"]), look_check=True)
    assert "speech balloon" in p and "blue shield with three gold towers" in p and "a tabard" in p
    assert "not visible" in p.lower()
    off = inspect_prompt(bible.match(["Pentik"]), look_check=False)
    assert "speech balloon" in off and "a tabard" not in off


def test_events_prompt_uses_speaker_tags_as_characters():
    p = events_prompt("Aino (Pentik): Hyökkään!", campaign())
    assert "Speaker (Character)" in p


def test_description_draft_asks_for_exact_must_haves():
    p = describe_prompt("Pentik", "")
    assert "shape, colour, material, number and position" in p
    assert "exactly one spiked iron ball" in p  # counts are spelled out
    assert "not height in numbers" in p  # height has its own field


def test_sheet_prompt_includes_the_never_list():
    bible = campaign()
    pentik = bible.characters[0]
    pentik.never = ["a tabard or cloth over the breastplate"]
    assert "Never: a tabard or cloth over the breastplate" in sheet_prompt(pentik, bible)


def test_the_script_writer_knows_each_characters_must_haves_and_never_list():
    # The test comic's script had Pentik "pointing his sword" while his spec says flail.
    bible = campaign()
    bible.characters[0].traits = ["flail"]
    bible.characters[0].never = ["a sword"]
    context = bible_context(bible)
    assert "Pentik: great helm. Must have: flail. Never: a sword" in context
    p = script_prompt("1. Fight", [Moment(title="T")], bible)
    assert "Never: a sword" in p and "must-haves" in p.lower()


def test_draw_prompt_states_sizes_of_the_characters_on_the_page():
    bible = campaign()
    bible.characters[0].height_cm = 185  # Pentik
    bible.characters[1].height_cm = 60  # Rintaro, on the page as "Rin"
    bible.characters.append(Character(id="kal", name="Käl", height_cm=160))  # not on the page
    p = draw_prompt(page(), bible, labelled(bible, [], []))
    assert "Sizes (these win over the reference images" in p
    assert "RINTARO (60 cm) is 32% of PENTIK's height" in p
    assert "KÄL" not in p


def test_draw_prompt_without_heights_has_no_size_line():
    assert "Sizes" not in draw_prompt(page(), campaign(), [])


def test_sheet_prompt_and_bible_context_carry_the_height():
    bible = campaign()
    rintaro = bible.characters[1]
    rintaro.height_cm = 60
    assert "Size: RINTARO is 60 cm tall" in sheet_prompt(rintaro, bible)
    assert "Rintaro (also: Rin): seal (height 60 cm)" in bible_context(bible)


def test_previous_page_never_outranks_the_references():
    bible = campaign()
    cast = labelled(bible, ["full-body sheet"], ["full-body sheet"])
    p = draw_prompt(page(), bible, cast, previous=True)
    previous = p[p.index("PREVIOUS PAGE") :].split("\n")[0]
    assert "keep every character's look" not in p
    assert "follow the references, not the previous page" in previous
    authority = p[p.index("Order of authority") :]
    assert authority.index("reference images") < authority.index("previous page")


def test_draw_prompt_holds_counts_and_materials():
    p = draw_prompt(page(), campaign(), [])
    assert "exactly the number of parts" in p and "material" in p


def test_sheet_prompt_keeps_the_natural_stance():
    bible = campaign()
    p = sheet_prompt(bible.characters[1], bible)
    assert "standing in a neutral pose" not in p
    assert "natural stance" in p and "short legs" in p and "don't stretch or slim it" in p
    assert "flippers" not in p  # an example must not describe a real character wrongly


def test_detail_sheet_keeps_the_number_of_parts():
    bible = campaign()
    p = detail_sheet_prompt(bible.characters[0], bible)
    assert "same number of" in p and "don't add heads" in p


def test_script_writer_names_items_exactly_and_keeps_bodies():
    p = script_prompt("events", [Moment(title="x")], campaign())
    assert 'never by a generic or different word ("weapon", "sword"' in p
    assert "posture (on two feet or on all fours), build and proportions" in p
    assert "the heights given" in p


def test_look_check_checks_sizes_only_with_two_heights():
    bible = campaign()
    bible.characters[0].height_cm = 185
    bible.characters[1].height_cm = 60
    p = inspect_prompt(bible.characters, look_check=True)
    assert "Sizes, where these characters stand side by side" in p
    assert "RINTARO (60 cm) is 32% of PENTIK's height" in p
    bible.characters[0].height_cm = None
    assert "Sizes" not in inspect_prompt(bible.characters, look_check=True)
    bible.characters[0].height_cm = 185
    assert "Sizes" not in inspect_prompt(bible.characters, look_check=False)


# --- who is on the page, per-panel identities, how images are sent ---------------------------

from app.llm import page_cast  # noqa: E402


def test_page_cast_adds_speakers_and_characters_named_in_panels():
    bible = campaign()
    bible.characters.append(Character(id="kal", name="Käl"))
    p = page()
    p.characters = ["Pentik"]  # the script writer forgot Rintaro and Käl
    p.panels[1].visual = "Rintaro strikes while Käl ducks"
    assert [c.id for c in page_cast(p, bible)] == ["pentik", "rintaro", "kal"]


def test_each_panel_names_who_is_in_it_and_their_references():
    bible = campaign()
    p = page()
    p.panels[1].balloons[0].speaker = "Rin"  # an alias as the speaker
    cast = labelled(bible, ["full-body sheet", "close-ups of details"], ["reference image"])
    text = draw_prompt(p, bible, cast)
    panel1, panel2 = text.split("Panel 1:")[1].split("Panel 2:")
    assert "In this panel: PENTIK (reference images 1-2)" in panel1
    assert 'Balloons: PENTIK: "Ota tuo elävänä!"' in panel1
    assert "In this panel: RINTARO (reference image 3)" in panel2
    assert 'RINTARO: "Hups.' in panel2  # the alias became the name the references use
    assert "unnamed extra" in text and "never a look-alike" in text


def test_a_character_without_references_is_drawn_from_the_rules():
    bible = campaign()
    bible.characters[1].traits = ["spotted grey seal"]
    text = draw_prompt(page(), bible, labelled(bible, ["full-body sheet"], []))
    assert "RINTARO has no reference image: draw them from these rules" in text
    assert "RINTARO must have: spotted grey seal" in text
    assert "In this panel: RINTARO (no reference image: draw from the rules)" in text


def test_the_script_writer_names_everyone_in_each_visual():
    p = script_prompt("events", [Moment(title="x")], campaign())
    assert "Name every player\n  character in a panel's visual" in p
    assert "character_focus" not in p


class Capture:
    """A fake genai.Client that records the request and answers with one image."""

    def __init__(self, seen):
        self.seen = seen

    def __call__(self, api_key, http_options=None):
        seen = self.seen

        class Models:
            def generate_content(self, **kwargs):
                seen.update(kwargs)
                part = type("P", (), {"inline_data": type("D", (), {"data": b"img"})()})()
                content = type("C", (), {"parts": [part]})()
                return type(
                    "R",
                    (),
                    {"candidates": [type("X", (), {"content": content})()], "usage_metadata": None},
                )()

        return type("Client", (), {"models": Models()})()


def big_png(size=(3000, 2000)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "red").save(buf, "PNG")
    return buf.getvalue()


def test_draw_page_puts_each_label_next_to_its_image_and_asks_for_2k(monkeypatch):
    from app import llm

    seen = {}
    monkeypatch.setattr(llm.genai, "Client", Capture(seen))
    bible = campaign()
    pentik, rintaro = bible.characters
    cast = [(pentik, [("full-body sheet", big_png())]), (rintaro, [("reference image", big_png())])]
    llm.draw_page(page(), bible, cast, previous=big_png())
    contents = seen["contents"]
    assert contents[0] == "Reference image 1: PENTIK (full-body sheet)"
    assert contents[2] == "Reference image 2: RINTARO (reference image)"
    assert contents[4] == "Reference image 3: PREVIOUS PAGE"
    assert "Reference image 3 is the PREVIOUS PAGE" in contents[6]
    image = contents[1].inline_data
    assert image.mime_type == "image/jpeg"
    assert max(Image.open(io.BytesIO(image.data)).size) == llm.INPUT_SIDE
    assert seen["config"].image_config.image_size == "2K"
    assert seen["config"].image_config.aspect_ratio == "3:4"


def test_the_image_size_can_be_left_to_the_model(monkeypatch):
    from app import llm

    seen = {}
    monkeypatch.setattr(llm.genai, "Client", Capture(seen))
    monkeypatch.setattr(llm.settings, "gemini_image_size", "")
    llm.draw_sheet(campaign().characters[1], [big_png((64, 64))], campaign())
    assert seen["config"].image_config.image_size is None
    assert seen["contents"][0] == "Reference image 1 of 1: Rintaro"
