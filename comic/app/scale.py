"""Relative heights: turn the heights set in the bible into plain size rules for one image.

Image models don't do arithmetic with centimetres, so each comparison is spelled out against
the tallest character present, as a percentage and as the body landmark the top of the shorter
character's head reaches ("…reaches PENTIK's hips").
"""

import re

from app.bible import Character

# Where on a standing adult's body a height ratio lands (fraction of total height).
_LANDMARKS = [
    (0.92, "eye level"),
    (0.87, "chin"),
    (0.81, "shoulders"),
    (0.72, "chest"),
    (0.62, "waist"),
    (0.52, "hips"),
    (0.40, "mid-thigh"),
    (0.28, "knees"),
    (0.15, "shins"),
]
SAME = 0.97  # within 3 %: "about the same height"
ADULT_HUMAN_CM = 175  # yardstick when a character is drawn alone (character sheets)
_HEIGHT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(cm|m)?\s*$", re.I)


def parse_height(text: str) -> float | None:
    """'60', '60 cm', '1.8 m' or '1,8 m' → centimetres; empty → None. Raises ValueError."""
    if not text.strip():
        return None
    match = _HEIGHT.match(text)
    if not match:
        raise ValueError(f"Height must be a number of cm, e.g. 60 or 1.8 m (got {text!r})")
    value = float(match.group(1).replace(",", "."))
    cm = value * 100 if (match.group(2) or "").lower() == "m" else value
    if not 1 <= cm <= 2000:
        raise ValueError(f"Height must be between 1 and 2000 cm (got {cm:g})")
    return cm


def _reaches(ratio: float) -> str:
    for threshold, landmark in _LANDMARKS:
        if ratio >= threshold:
            return landmark
    return "ankles"


def _cm(value: float) -> str:
    return f"{value:g} cm"


def scale_line(characters: list[Character]) -> str:
    """Size rules for the characters in one image; empty if fewer than one has a height."""
    known = sorted(
        (c for c in characters if c.height_cm), key=lambda c: c.height_cm or 0, reverse=True
    )
    if not known:
        return ""
    if len(known) == 1:
        c = known[0]
        ratio = (c.height_cm or 0) / ADULT_HUMAN_CM
        if ratio >= SAME:
            compare = f"{ratio:.1f}× the height of an adult human"
        else:
            compare = f"the top of the head reaches an adult human's {_reaches(ratio)}"
        return f"{c.name.upper()} is {_cm(c.height_cm or 0)} tall: {compare}."
    tallest, rest = known[0], known[1:]
    big = tallest.name.upper()
    lines = [f"{big} is the tallest ({_cm(tallest.height_cm or 0)})."]
    for c in rest:
        ratio = (c.height_cm or 0) / (tallest.height_cm or 1)
        name = c.name.upper()
        if ratio >= SAME:
            lines.append(f"{name} ({_cm(c.height_cm or 0)}) is about the same height as {big}.")
        else:
            lines.append(
                f"{name} ({_cm(c.height_cm or 0)}) is {round(ratio * 100)}% of {big}'s height: "
                f"the top of {name}'s head reaches {big}'s {_reaches(ratio)}."
            )
    lines.append(
        "Keep these relative sizes in every panel where they appear together, apart from "
        "perspective (nearer figures look larger)."
    )
    return " ".join(lines)
