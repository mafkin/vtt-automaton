"""Comics in the making: one per transcript, built step by step on the dashboard.

data/comic/comics/<id>/comic.json holds the state (events, chosen moments, the script you edit,
page images and their versions, tokens spent); page images sit next to it. data/comic/limits.json
holds the token budget, a failsafe so a broken run can't spend a month of credit.
"""

import json
import re
import secrets
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.files import write_atomic

# new → extracting → events → scripting → script → drawing → done; failed/budget on the way.
Status = Literal[
    "new", "extracting", "events", "scripting", "script", "drawing", "done", "failed", "budget"
]
BUSY: set[str] = {"extracting", "scripting", "drawing"}

_ID = re.compile(r"[a-z0-9-]{1,80}")
_PAGE_FILE = re.compile(r"page_\d+_v\d+\.png")


class BudgetExceeded(RuntimeError):
    pass


class Limits(BaseModel):
    # Tokens one comic may spend on drawing: page images and their lettering checks. Reading
    # the transcript and writing the script are single calls; they're counted, not budgeted.
    token_budget_per_comic: int = 80_000
    # Automatic redraws of a page whose lettering doesn't match the script.
    max_auto_redraws_per_page: int = 1


class Moment(BaseModel):
    title: str
    summary: str = ""
    characters: list[str] = Field(default_factory=list)


class Balloon(BaseModel):
    speaker: str
    text: str


class ScriptPanel(BaseModel):
    visual: str
    balloons: list[Balloon] = Field(default_factory=list)


class ScriptPage(BaseModel):
    title: str
    characters: list[str] = Field(default_factory=list)
    panels: list[ScriptPanel]


class PageState(BaseModel):
    versions: list[str] = Field(default_factory=list)
    # Result of reading the lettering back: "ok", or what didn't match.
    check: str = ""

    @property
    def current(self) -> str | None:
        return self.versions[-1] if self.versions else None


class Comic(BaseModel):
    id: str
    session_id: str
    label: str
    created_at: float
    status: Status = "new"
    message: str = ""
    events: str = ""
    moments: list[Moment] = Field(default_factory=list)
    script: list[ScriptPage] = Field(default_factory=list)
    pages: list[PageState] = Field(default_factory=list)
    # Drawing (page images + lettering checks): counts against the budget.
    image_tokens: int = 0
    # Reading the transcript and writing the script: shown, not budgeted.
    text_tokens: int = 0


def _root() -> Path:
    return Path(settings.bible_dir) / "comics"


def _dir(comic_id: str) -> Path:
    if not _ID.fullmatch(comic_id):
        raise KeyError(comic_id)
    return _root() / comic_id


def save(comic: Comic) -> None:
    text = json.dumps(comic.model_dump(), ensure_ascii=False, indent=2)
    write_atomic(_dir(comic.id) / "comic.json", text.encode("utf-8"))


def create(session_id: str, label: str) -> Comic:
    stamp = time.strftime("%Y%m%d-%H%M")
    comic_id = f"{re.sub(r'[^a-z0-9]+', '-', session_id.lower()).strip('-')[:40]}-{stamp}"
    comic_id = f"{comic_id}-{secrets.token_hex(2)}"
    comic = Comic(id=comic_id, session_id=session_id, label=label, created_at=time.time())
    save(comic)
    return comic


def load(comic_id: str) -> Comic:
    try:
        return Comic.model_validate_json((_dir(comic_id) / "comic.json").read_text("utf-8"))
    except FileNotFoundError as exc:
        raise KeyError(comic_id) from exc


def list_comics() -> list[Comic]:
    if not _root().exists():
        return []
    found = [load(p.parent.name) for p in _root().glob("*/comic.json")]
    return sorted(found, key=lambda c: c.created_at, reverse=True)


def add_page_version(comic: Comic, index: int, png: bytes) -> str:
    """Store a new drawing of page `index` (0-based); older versions are kept."""
    while len(comic.pages) <= index:
        comic.pages.append(PageState())
    page = comic.pages[index]
    name = f"page_{index + 1}_v{len(page.versions) + 1}.png"
    write_atomic(_dir(comic.id) / name, png)
    page.versions.append(name)
    save(comic)
    return name


def page_path(comic_id: str, name: str) -> Path:
    if not _PAGE_FILE.fullmatch(name):
        raise KeyError(name)
    path = _dir(comic_id) / name
    if not path.exists():
        raise KeyError(name)
    return path


def load_limits() -> Limits:
    try:
        return Limits.model_validate_json((_root().parent / "limits.json").read_text("utf-8"))
    except FileNotFoundError:
        return Limits()


def save_limits(limits: Limits) -> None:
    write_atomic(_root().parent / "limits.json", limits.model_dump_json(indent=2).encode())


def charge(comic: Comic, tokens: int | None, kind: Literal["image", "text"]) -> None:
    """Add a Gemini call's total tokens to the comic and save it."""
    if kind == "image":
        comic.image_tokens += tokens or 0
    else:
        comic.text_tokens += tokens or 0
    save(comic)


def ensure_budget(comic: Comic, estimate: int) -> None:
    """Refuse a drawing call that could take the comic over its budget."""
    budget = load_limits().token_budget_per_comic
    if comic.image_tokens + estimate > budget:
        raise BudgetExceeded(
            f"Budget reached ({comic.image_tokens} / {budget} tokens) – raise the limit "
            "or redraw fewer pages"
        )
