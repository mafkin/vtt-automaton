"""Detect spoken rules questions ("Nethys, voiko …") in live transcript segments."""

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

_PUNCT_RE = re.compile(r"[^\w]+", re.UNICODE)
_LEAD_STRIP = " \t,.:;!?-–—\"'"
_TRAIL_STRIP = " \t,.:;-–—\"'"  # keep a trailing "?" / "!"

# Saying one of these right after the wake word asks for a GM-only (whispered) ruling.
SECRET_MARKERS = frozenset({"salaa", "salainen", "salaisesti", "secret", "secretly"})


@dataclass(frozen=True)
class VoiceRequest:
    query: str
    mode: Literal["public", "gm"]


def _norm(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word.lower())
    return _PUNCT_RE.sub("", "".join(c for c in folded if not unicodedata.combining(c)))


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class WakeWordDetector:
    """Per-speaker wake word detection.

    A segment containing the wake word yields a request for the words after it. If nothing (or
    too little) follows, the same speaker's next segment within ``followup_seconds`` is the
    question ("Nethys…" pause "voiko …").
    """

    wake_words: list[str]
    threshold: float = 0.8
    followup_seconds: float = 8.0
    min_query_words: int = 2
    _awaiting: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._wake = [_norm(w) for w in self.wake_words if _norm(w)]

    def _is_wake(self, word: str) -> bool:
        token = _norm(word)
        if not token:
            return False
        for wake in self._wake:
            # Also compare a prefix, so inflected forms match: "Nethysiltä", "Nethysin".
            prefix = token[: len(wake) + 1]
            if max(_similar(token, wake), _similar(prefix, wake)) >= self.threshold:
                return True
        return False

    def _build(self, words: list[str]) -> VoiceRequest | None:
        mode: Literal["public", "gm"] = "public"
        if words and _norm(words[0]) in SECRET_MARKERS:
            mode = "gm"
            words = words[1:]
        query = " ".join(words).lstrip(_LEAD_STRIP).rstrip(_TRAIL_STRIP)
        if len(query.split()) < self.min_query_words:
            return None
        return VoiceRequest(query=query, mode=mode)

    def feed(self, speaker: str, text: str, t: float) -> VoiceRequest | None:
        words = text.split()
        wake_at = next((i for i, w in enumerate(words) if self._is_wake(w)), None)

        if wake_at is None:
            since = self._awaiting.pop(speaker, None)
            if since is not None and t - since <= self.followup_seconds:
                return self._build(words)
            return None

        request = self._build(words[wake_at + 1 :])
        if request is None:
            self._awaiting[speaker] = t
        else:
            self._awaiting.pop(speaker, None)
        return request
