"""Queue of utterances: one at a time through the model, results posted to the backend."""

import asyncio
import contextlib
import logging
from dataclasses import dataclass

import httpx

from app.transcriber import Transcriber

log = logging.getLogger(__name__)

_RETRY_DELAYS = (1, 2, 4, 8)


@dataclass(frozen=True)
class Utterance:
    session_id: str
    speaker: str
    speaker_id: str | None
    character: str | None
    t_start: float
    duration: float
    wav: bytes


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
        # The model call blocks for a second or two; keep the HTTP server responsive meanwhile.
        text = await asyncio.to_thread(self._transcriber.transcribe, u.wav)
        self.processed += 1
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
