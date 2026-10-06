import pytest

from app.bible import Character
from app.scale import parse_height, scale_line


def ch(name: str, height: float | None) -> Character:
    return Character(id=name.lower(), name=name, height_cm=height)


@pytest.mark.parametrize(
    "text, cm",
    [("60", 60), ("60 cm", 60), (" 175cm ", 175), ("1.8 m", 180), ("1,8 m", 180), ("", None)],
)
def test_parse_height(text, cm):
    assert parse_height(text) == cm


@pytest.mark.parametrize("text", ["tall", "-5", "0", "3000", "60 ft"])
def test_parse_height_rejects_nonsense(text):
    with pytest.raises(ValueError):
        parse_height(text)


def test_cast_is_compared_to_the_tallest_present():
    line = scale_line([ch("Rintaro", 60), ch("Pentik", 185), ch("Käl", 160)])
    assert line.startswith("PENTIK is the tallest (185 cm).")
    assert (
        "KÄL (160 cm) is 86% of PENTIK's height: the top of KÄL's head reaches PENTIK's shoulders."
        in line
    )
    assert (
        "RINTARO (60 cm) is 32% of PENTIK's height: "
        "the top of RINTARO's head reaches PENTIK's knees." in line
    )
    assert "perspective" in line


def test_the_comparison_follows_who_is_in_the_image():
    # Without Pentik, Käl becomes the yardstick.
    line = scale_line([ch("Rintaro", 60), ch("Käl", 160)])
    assert line.startswith("KÄL is the tallest (160 cm).")
    assert "RINTARO (60 cm) is 38% of KÄL's height" in line
    assert "PENTIK" not in line


def test_near_equal_heights():
    line = scale_line([ch("Jessan", 178), ch("Pentik", 180)])
    assert "JESSAN (178 cm) is about the same height as PENTIK." in line


def test_alone_compared_to_an_adult_human():
    assert scale_line([ch("Rintaro", 60)]) == (
        "RINTARO is 60 cm tall: the top of the head reaches an adult human's knees."
    )
    assert "1.3× the height of an adult human" in scale_line([ch("Oakrend", 230)])


def test_characters_without_height_are_left_out():
    assert scale_line([ch("Pentik", None)]) == ""
    assert scale_line([ch("Pentik", None), ch("Rintaro", 60)]).startswith("RINTARO is 60 cm tall")
