"""The comic bible: campaign context, art style and characters, carried from comic to comic.

Stored on the data volume as data/comic/bible.json, with character reference images under
data/comic/characters/<id>/. Edited from the dashboard; each comic job reads it at the start.
"""

import io
import json
import os
import re
import secrets
import shutil
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.config import settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 1024
_IMAGE_NAME = re.compile(r"[0-9a-f]{16}\.png")


class BibleError(ValueError):
    """Bad input from the dashboard, such as an upload that isn't an image."""


class Style(BaseModel):
    positive: str = "comic book illustration, bold ink lines, flat colours"
    negative: str = "bad hands, text, watermark, signature, error"


class Character(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    # Short visual description added to every panel this character is in (SDXL: keep it short).
    appearance: str = ""
    images: list[str] = Field(default_factory=list)


class Bible(BaseModel):
    setting: str = ""
    tone: str = ""
    bubble_language: str = "Finnish"
    style: Style = Field(default_factory=Style)
    characters: list[Character] = Field(default_factory=list)

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
    """Write atomically, so the worker never reads a half-written file."""
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(bible.model_dump(), ensure_ascii=False, indent=2))
    os.replace(tmp, root / "bible.json")


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


def update_character(character_id: str, name: str, aliases: list[str], appearance: str) -> None:
    bible = load()
    character = bible.character(character_id)
    character.name = name.strip() or character.name
    character.aliases = [a.strip() for a in aliases if a.strip()]
    character.appearance = appearance.strip()
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
