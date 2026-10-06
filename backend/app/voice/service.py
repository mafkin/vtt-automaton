"""Turn spoken rules questions from the live transcript into rulings posted in Foundry."""

import asyncio
import logging
import time
import uuid
from collections import deque

from pydantic import BaseModel, Field

from app.foundry.hub import FoundryHub
from app.foundry.messages import run_ruling
from app.rules.models import RulingContext, RulingRequest
from app.rules.service import RulesService
from app.voice.trigger import VoiceRequest, WakeWordDetector, _norm

log = logging.getLogger(__name__)


class TranscriptSegment(BaseModel):
    speaker: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=4000)
    # Unix time the speech ended; defaults to when the segment is received.
    t: float | None = None
    # Set by the transcription pipeline; a hand-sent test segment can leave them out.
    session_id: str | None = Field(default=None, max_length=64)
    speaker_id: str | None = Field(default=None, max_length=64)
    character: str | None = Field(default=None, max_length=100)
    t_start: float | None = None

    @property
    def label(self) -> str:
        return f"{self.speaker} ({self.character})" if self.character else self.speaker


class VoiceRulesService:
    def __init__(
        self,
        rules: RulesService,
        hub: FoundryHub,
        language: str,
        wake_words: list[str],
        context_seconds: float = 90,
        cooldown_seconds: float = 30,
    ) -> None:
        self._rules = rules
        self._hub = hub
        self._language = language
        self._detector = WakeWordDetector(wake_words)
        self._context_seconds = context_seconds
        self._cooldown_seconds = cooldown_seconds
        self._recent: deque[tuple[float, str]] = deque()
        self._last_asked: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()

    def handle_segment(self, segment: TranscriptSegment) -> str | None:
        """Record a transcript segment; if it asks the arbiter something, start a ruling.

        Returns the ruling id when one was started.
        """
        t = segment.t if segment.t is not None else time.time()
        context = self._context_before(t)
        self._recent.append((t, f"{segment.label}: {segment.text.strip()}"))

        request = self._detector.feed(segment.speaker, segment.text, t)
        if request is None:
            return None

        key = _norm(request.query)
        # Forget questions older than the cooldown so the map can't grow over a long uptime.
        self._last_asked = {
            k: asked for k, asked in self._last_asked.items() if t - asked < self._cooldown_seconds
        }
        if t - self._last_asked.get(key, float("-inf")) < self._cooldown_seconds:
            log.info("Ignoring repeated voice question %r", request.query)
            return None
        if not self._hub.connected:
            log.info("Voice question heard but no Foundry GM client is connected")
            return None
        self._last_asked[key] = t

        ruling_id = f"voice-{uuid.uuid4().hex[:12]}"
        log.info("Spoken question %s from %s: %r", ruling_id, segment.speaker, request.query)
        task = asyncio.create_task(
            self._run(ruling_id, segment.speaker, request, context, spoken_at=t)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return ruling_id

    def _context_before(self, t: float) -> list[str]:
        while self._recent and t - self._recent[0][0] > self._context_seconds:
            self._recent.popleft()
        return [line for _, line in self._recent]

    async def _run(
        self,
        ruling_id: str,
        speaker: str,
        request: VoiceRequest,
        context: list[str],
        spoken_at: float,
    ) -> None:
        meta = {"origin": "voice", "speaker": speaker, "query": request.query, "mode": request.mode}
        await self._hub.broadcast({"type": "ruling.pending", "id": ruling_id, **meta})
        ruling_request = RulingRequest(
            query=request.query,
            mode=request.mode,
            user=speaker,
            context=RulingContext(transcript=context),
            render="foundry",
        )
        reply = await run_ruling(self._rules, self._language, ruling_request, ruling_id)
        await self._hub.broadcast({**reply, **meta})
        # End to end: from the end of the sentence (speech-to-text included) to the Foundry card.
        log.info(
            "Spoken question %s answered %.1f s after the speaker finished",
            ruling_id,
            time.time() - spoken_at,
        )
