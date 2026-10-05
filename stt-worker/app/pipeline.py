"""Queue of utterances: one at a time through the model, results posted to the backend."""

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from app.transcriber import Transcriber

log = logging.getLogger(__name__)

_RETRY_DELAYS = (1, 2, 4, 8)

# Waiting longer than this in the queue means transcription is falling behind the table.
BACKLOG_WARN_SECONDS = 5.0
_BACKLOG_WARN_INTERVAL = 60.0


@dataclass(frozen=True)
class Utterance:
    session_id: str
    speaker: str
    speaker_id: str | None
    character: str | None
    t_start: float
    duration: float
    wav: bytes
    queued_at: float = field(default_factory=time.monotonic, compare=False)


@dataclass(frozen=True)
class Timing:
    audio: float  # seconds of speech
    wait: float  # seconds spent in the queue
    work: float  # seconds the model took


class Stats:
    """Rolling timing figures for the most recent utterances (shown in /healthz)."""

    def __init__(self, size: int = 100) -> None:
        self._recent: deque[Timing] = deque(maxlen=size)

    def add(self, timing: Timing) -> None:
        self._recent.append(timing)

    def summary(self) -> dict:
        if not self._recent:
            return {"utterances": 0}
        audio = sum(t.audio for t in self._recent)
        work = sum(t.work for t in self._recent)
        waits = [t.wait for t in self._recent]
        return {
            "utterances": len(self._recent),
            "audio_seconds": round(audio, 1),
            # How many seconds of speech one second of model time handles; below 1 can't keep up.
            "speed_x_realtime": round(audio / work, 1) if work else None,
            "avg_wait_seconds": round(sum(waits) / len(waits), 2),
            "max_wait_seconds": round(max(waits), 2),
        }


class QueueFull(Exception):
    pass


class Pipeline:
    def __init__(
        self,
        transcriber: Transcriber,
        backend: httpx.AsyncClient,
        max_queue: int = 200,
    ) -> None:
        self._transcriber = transcriber
        self._backend = backend
        self._queue: asyncio.Queue[Utterance] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None
        self.processed = 0
        self.dropped_empty = 0
        self.stats = Stats()
        self._last_backlog_warning = float("-inf")

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    def submit(self, utterance: Utterance) -> None:
        try:
            self._queue.put_nowait(utterance)
        except asyncio.QueueFull:
            raise QueueFull from None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def drain(self) -> None:
        """Wait until every queued utterance has been handled (used by tests)."""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            utterance = await self._queue.get()
            try:
                await self._handle(utterance)
            except Exception:
                log.exception("Failed to handle utterance from %s", utterance.speaker)
            finally:
                self._queue.task_done()

    async def _handle(self, u: Utterance) -> None:
        started = time.monotonic()
        wait = started - u.queued_at
        # The model call blocks for a second or two; keep the HTTP server responsive meanwhile.
        text = await asyncio.to_thread(self._transcriber.transcribe, u.wav)
        work = time.monotonic() - started
        self.processed += 1
        self.stats.add(Timing(audio=u.duration, wait=wait, work=work))
        log.info(
            "Transcribed %.1f s from %s in %.2f s (%.1fx real time), waited %.1f s, %d queued: %s",
            u.duration,
            u.speaker,
            work,
            u.duration / work if work > 0 else float("inf"),
            wait,
            self.queued,
            f"{len(text)} chars" if text else "nothing kept",
        )
        if (
            wait > BACKLOG_WARN_SECONDS
            and started - self._last_backlog_warning > _BACKLOG_WARN_INTERVAL
        ):
            self._last_backlog_warning = started
            log.warning(
                "Transcription is falling behind: a clip waited %.1f s with %d still queued. "
                "Consider STT_BEAM_SIZE=1 or STT_COMPUTE_TYPE=int8_float16.",
                wait,
                self.queued,
            )
        if not text:
            self.dropped_empty += 1
            return
        await self._post(
            {
                "session_id": u.session_id,
                "speaker": u.speaker,
                "speaker_id": u.speaker_id,
                "character": u.character,
                "t_start": u.t_start,
                "t": u.t_start + u.duration,
                "text": text,
            }
        )

    async def _post(self, segment: dict) -> None:
        for attempt, delay in enumerate((0, *_RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self._backend.post("/api/v1/transcript/segments", json=segment)
            except httpx.HTTPError as exc:
                log.warning("Backend unreachable (attempt %d): %s", attempt + 1, exc)
                continue
            if response.status_code < 500:
                if response.is_error:
                    log.error("Backend refused segment: %s %s", response.status_code, response.text)
                return
            log.warning("Backend error %s (attempt %d)", response.status_code, attempt + 1)
        log.error("Gave up posting a segment from %s", segment["speaker"])
