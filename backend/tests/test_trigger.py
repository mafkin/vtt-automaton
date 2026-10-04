import pytest

from app.voice.trigger import VoiceRequest, WakeWordDetector


@pytest.fixture
def detector():
    return WakeWordDetector(["Nethys"])


@pytest.mark.parametrize(
    "text, query",
    [
        ("Nethys, voiko Trip tehdä ilman vapaata kättä?", "voiko Trip tehdä ilman vapaata kättä?"),
        ("okei kysytään Nethysiltä mitä off-guard tekee", "mitä off-guard tekee"),
        ("NETHYS: what does prone do", "what does prone do"),
        ("Nethis voiko villisika tehdä Trample kahdesti", "voiko villisika tehdä Trample kahdesti"),
    ],
)
def test_wake_word_variants(detector, text, query):
    assert detector.feed("Aino", text, 0) == VoiceRequest(query=query, mode="public")


@pytest.mark.parametrize(
    "text",
    [
        "luin netissä että tämä toimii",
        "netti on hidas tänään",
        "mennään eteenpäin, Strike ja sitten Stride",
        "",
    ],
)
def test_ordinary_talk_does_not_trigger(detector, text):
    assert detector.feed("Aino", text, 0) is None


def test_secret_marker_requests_gm_only_ruling(detector):
    request = detector.feed("GM", "Nethys, salaa: näkeekö hiipijä minut?", 0)
    assert request == VoiceRequest(query="näkeekö hiipijä minut?", mode="gm")


def test_question_after_a_pause_uses_next_segment(detector):
    assert detector.feed("Aino", "Nethys...", 10) is None
    assert detector.feed("Ville", "odota hetki", 11) is None  # other speakers don't consume it
    assert detector.feed("Aino", "voinko tehdä Shield Block?", 13) == VoiceRequest(
        query="voinko tehdä Shield Block?", mode="public"
    )
    assert detector.feed("Aino", "ja sitten jotain muuta", 14) is None


def test_follow_up_expires(detector):
    detector.feed("Aino", "Nethys", 10)
    assert detector.feed("Aino", "voinko tehdä Shield Block?", 30) is None


def test_too_short_question_waits_for_more(detector):
    assert detector.feed("Aino", "Nethys, flanking?", 0) is None
    assert detector.feed("Aino", "miten flanking toimii", 2) is not None
