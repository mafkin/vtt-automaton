"""Speech-to-text with faster-whisper, plus filtering of Whisper's typical hallucinations."""

import io
import logging
import re
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# Whisper was trained on subtitled video, so on noise or silence it tends to "hear" subtitle
# credits and sign-offs. These are dropped when they make up the whole utterance.
HALLUCINATIONS = [
    r"kiitos (kun )?katso(i|it|itte|mises)\w*",
    r"kiitos( paljon)?\.?",
    r"(suomenkieli\w* )?tekstit(ys)?\b.*",
    r"tekstitys:.*",
    r"thank(s| you) for watching.*",
    r"subtitles by.*",
    r"\.+",
]
_HALLUCINATION_RE = re.compile(r"^\s*(?:" + "|".join(HALLUCINATIONS) + r")\s*[.!]*\s*$", re.I)

# Segment-level confidence limits (faster-whisper segment fields).
MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.0


@dataclass(frozen=True)
class Piece:
    """One faster-whisper segment, reduced to what the filter needs."""

    text: str
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def clean_transcript(pieces: list[Piece], prompt: str = "") -> str:
    """Join confident pieces and drop known hallucinations. Returns "" when nothing is left.

    On noise or silence Whisper often repeats its initial prompt back, so a piece that is just
    a part of the prompt is dropped too.
    """
    prompt_norm = _norm(prompt)
    kept = [
        p.text.strip()
        for p in pieces
        if _norm(p.text)
        and not (p.no_speech_prob > MAX_NO_SPEECH_PROB and p.avg_logprob < MIN_AVG_LOGPROB)
        and not (prompt_norm and _norm(p.text) in prompt_norm)
    ]
    text = " ".join(kept).strip()
    if not text or _HALLUCINATION_RE.match(text):
        return ""
    return text


def build_prompt(terms: list[str]) -> str:
    """Initial prompt biasing Whisper towards the table's vocabulary (it reads as prior context)."""
    return "Pathfinder-roolipeli suomeksi. Sanastoa: " + ", ".join(terms) + "."


class Transcriber(Protocol):
    def transcribe(self, wav: bytes) -> str: ...


class WhisperTranscriber:
    """faster-whisper model held in memory; ``transcribe`` is blocking and not thread-safe."""

    def __init__(
        self,
        model: str,
        device: str,
        compute_type: str,
        language: str,
        beam_size: int,
        prompt: str,
    ) -> None:
        from faster_whisper import WhisperModel  # heavy import, only when actually used

        log.info("Loading Whisper model %s on %s (%s)", model, device, compute_type)
        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self._language = language
        self._beam_size = beam_size
        self._prompt = prompt

    def transcribe(self, wav: bytes) -> str:
        segments, _ = self._model.transcribe(
            io.BytesIO(wav),
            language=self._language,
            beam_size=self._beam_size,
            initial_prompt=self._prompt,
            # Each utterance stands alone; carrying text over makes errors snowball.
            condition_on_previous_text=False,
            # Discord already cut the audio at pauses.
            vad_filter=False,
        )
        pieces = [Piece(s.text, s.no_speech_prob, s.avg_logprob) for s in segments]
        return clean_transcript(pieces, self._prompt)
