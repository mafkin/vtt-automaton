"""The comic bible: campaign context, art style and characters, carried from comic to comic.

Stored on the data volume as data/comic/bible.json, with character reference images under
data/comic/characters/<id>/. Edited from the dashboard; each comic job reads it at the start.
"""

import io
import json
import re
import secrets
import shutil
import unicodedata
from pathlib import Path
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.config import settings
from app.files import write_atomic

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 1024
_IMAGE_NAME = re.compile(r"[0-9a-f]{16}\.png")
_SHEET_NAME = re.compile(r"(sheet|detail)_\d+\.png")


class BibleError(ValueError):
    """Bad input from the dashboard, such as an upload that isn't an image."""


class Style(BaseModel):
    positive: str = "comic book illustration, bold ink lines, flat colours"
    negative: str = "bad hands, text, watermark, signature, error"
    # Lettering, borders and page colour: the same on every page.
    page_look: str = (
        "clean hand-lettered comic font, white balloons with black outlines, "
        "black panel borders, cream parchment page"
    )
    # An approved page (style/anchor.png) sent with every page as the style to match.
    anchor: str | None = None


class Character(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    # Short visual description given to the script writer and the page drawer, with the images.
    appearance: str = ""
    # Short must-haves the page drawer is told to keep, e.g. "red headband".
    traits: list[str] = Field(default_factory=list)
    # What the page drawer must never give this character, e.g. "a tabard".
    never: list[str] = Field(default_factory=list)
    # Standing height in cm. With heights set, every page states the relative sizes of the
    # characters on it (app/scale.py).
    height_cm: float | None = None
    images: list[str] = Field(default_factory=list)
    # Character sheets drawn in the comic's style; the approved one is the reference on pages.
    sheets: list[str] = Field(default_factory=list)
    sheet: str | None = None
    # Approved close-ups of the details (helm, emblem, shield, weapon): a second reference.
    detail: str | None = None
    # Sheets ever drawn: names aren't reused, so page versions that name a sheet stay right.
    sheet_count: int = 0


class Bible(BaseModel):
    setting: str = ""
    tone: str = ""
    bubble_language: str = "Finnish"
    style: Style = Field(default_factory=Style)
    characters: list[Character] = Field(default_factory=list)
    # Gemini tokens spent on the bible itself (character sheets, description drafts).
    tokens_used: int = 0

    def character(self, character_id: str) -> Character:
        for c in self.characters:
            if c.id == character_id:
                return c
        raise KeyError(character_id)

    def match(self, names: list[str]) -> list[Character]:
        """The characters these names refer to (by name or alias, ignoring case), in order."""
        found: list[Character] = []
        for name in names:
            key = name.strip().casefold()
            for c in self.characters:
                if key in {n.casefold() for n in (c.name, *c.aliases)} and c not in found:
                    found.append(c)
        return found


def _root() -> Path:
    return Path(settings.bible_dir)


def _character_dir(character_id: str) -> Path:
    return _root() / "characters" / character_id


def load() -> Bible:
    try:
        return Bible.model_validate_json((_root() / "bible.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Bible()


def save(bible: Bible) -> None:
    text = json.dumps(bible.model_dump(), ensure_ascii=False, indent=2)
    write_atomic(_root() / "bible.json", text.encode("utf-8"))


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "character"


def add_character(name: str) -> Character:
    bible = load()
    base = slugify(name)
    taken = {c.id for c in bible.characters}
    character_id, n = base, 2
    while character_id in taken:
        character_id, n = f"{base}-{n}", n + 1
    character = Character(id=character_id, name=name.strip())
    bible.characters.append(character)
    save(bible)
    return character


def update_character(
    character_id: str,
    name: str,
    aliases: list[str],
    appearance: str,
    traits: list[str] | None = None,
    never: list[str] | None = None,
    height_cm: float | None | Literal["keep"] = "keep",
) -> None:
    bible = load()
    character = bible.character(character_id)
    character.name = name.strip() or character.name
    character.aliases = [a.strip() for a in aliases if a.strip()]
    character.appearance = appearance.strip()
    if traits is not None:
        character.traits = [t.strip() for t in traits if t.strip()]
    if never is not None:
        character.never = [t.strip() for t in never if t.strip()]
    if height_cm != "keep":
        character.height_cm = height_cm
    save(bible)


def delete_character(character_id: str) -> None:
    bible = load()
    bible.characters.remove(bible.character(character_id))
    save(bible)
    shutil.rmtree(_character_dir(character_id), ignore_errors=True)


def add_image(character_id: str, data: bytes) -> str:
    """Store a reference image: re-encoded as PNG (drops metadata), longest side ≤1024 px."""
    bible = load()
    character = bible.character(character_id)
    if len(data) > MAX_UPLOAD_BYTES:
        raise BibleError(f"Image too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise BibleError("File is not an image (use PNG, JPEG or WebP)") from exc
    image = image.convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    name = f"{secrets.token_hex(8)}.png"
    folder = _character_dir(character_id)
    folder.mkdir(parents=True, exist_ok=True)
    image.save(folder / name, "PNG")
    character.images.append(name)
    save(bible)
    return name


def image_path(character_id: str, name: str) -> Path:
    """Path of a stored image. Only names the bible lists are accepted, so no path tricks."""
    character = load().character(character_id)
    if not _IMAGE_NAME.fullmatch(name) or name not in character.images:
        raise KeyError(name)
    return _character_dir(character_id) / name


def delete_image(character_id: str, name: str) -> None:
    path = image_path(character_id, name)
    bible = load()
    bible.character(character_id).images.remove(name)
    save(bible)
    path.unlink(missing_ok=True)


def add_sheet(character_id: str, png: bytes, kind: Literal["sheet", "detail"] = "sheet") -> str:
    """Store a drawn full-body sheet or detail sheet (not approved yet). Numbers are shared by
    both kinds and never reused."""
    bible = load()
    character = bible.character(character_id)
    character.sheet_count += 1
    name = f"{kind}_{character.sheet_count}.png"
    folder = _character_dir(character_id)
    write_atomic(folder / name, png)
    character.sheets.append(name)
    save(bible)
    return name


def sheet_path(character_id: str, name: str) -> Path:
    character = load().character(character_id)
    if not _SHEET_NAME.fullmatch(name) or name not in character.sheets:
        raise KeyError(name)
    return _character_dir(character_id) / name


def approve_sheet(character_id: str, name: str) -> None:
    sheet_path(character_id, name)
    bible = load()
    character = bible.character(character_id)
    if name.startswith("detail_"):
        character.detail = name
    else:
        character.sheet = name
    save(bible)


def delete_sheet(character_id: str, name: str) -> None:
    path = sheet_path(character_id, name)
    bible = load()
    character = bible.character(character_id)
    character.sheets.remove(name)
    if character.sheet == name:
        character.sheet = None
    if character.detail == name:
        character.detail = None
    save(bible)
    path.unlink(missing_ok=True)


def reference_image(character: Character) -> bytes | None:
    """What the page drawer sees of a character: the approved sheet, else the first image."""
    if character.sheet:
        return sheet_path(character.id, character.sheet).read_bytes()
    if character.images:
        return image_path(character.id, character.images[0]).read_bytes()
    return None


def references(character: Character) -> list[tuple[str, bytes]]:
    """What the page drawer sees of a character, labelled: the full-body reference, then the
    approved detail close-ups if any."""
    refs: list[tuple[str, bytes]] = []
    full = reference_image(character)
    if full is not None:
        refs.append(("full-body sheet" if character.sheet else "reference image", full))
    if character.detail:
        refs.append(
            ("close-ups of details", sheet_path(character.id, character.detail).read_bytes())
        )
    return refs


def _anchor_file() -> Path:
    return _root() / "style" / "anchor.png"


def set_anchor(png: bytes) -> None:
    write_atomic(_anchor_file(), png)
    bible = load()
    bible.style.anchor = "anchor.png"
    save(bible)


def clear_anchor() -> None:
    bible = load()
    bible.style.anchor = None
    save(bible)
    _anchor_file().unlink(missing_ok=True)


def anchor_image() -> bytes | None:
    if not load().style.anchor or not _anchor_file().exists():
        return None
    return _anchor_file().read_bytes()


def charge(tokens: int | None) -> None:
    bible = load()
    bible.tokens_used += tokens or 0
    save(bible)
