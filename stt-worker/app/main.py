import contextlib
import hmac
import io
import logging
import wave
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status

from app.config import Settings, get_settings
from app.pipeline import Pipeline, QueueFull, Utterance
from app.transcriber import Transcriber, WhisperTranscriber, build_prompt

log = logging.getLogger(__name__)


def wav_duration(wav: bytes) -> float:
    """Duration of a PCM WAV file in seconds (raises ValueError if it isn't one)."""
    try:
        with wave.open(io.BytesIO(wav)) as w:
            if not w.getframerate():
                raise ValueError("invalid WAV header")
            return w.getnframes() / w.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"not a PCM WAV file: {exc}") from None


def build_transcriber(settings: Settings) -> Transcriber:
    return WhisperTranscriber(
        model=settings.model,
        device=settings.device,
        compute_type=settings.compute_type,
        language=settings.language,
        beam_size=settings.beam_size,
        prompt=build_prompt(settings.prompt_terms),
    )


def create_app(
    settings: Settings | None = None,
    transcriber: Transcriber | None = None,
    backend: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if not settings.token:
        raise RuntimeError("STT_TOKEN must be set")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = backend or httpx.AsyncClient(
            base_url=settings.backend_url,
            headers={"Authorization": f"Bearer {settings.backend_token}"},
            timeout=30,
        )
        pipeline = Pipeline(transcriber or build_transcriber(settings), client, settings.max_queue)
        pipeline.start()
        app.state.pipeline = pipeline
        yield
        await pipeline.stop()
        if backend is None:
            await client.aclose()

    app = FastAPI(title="VTT Automaton STT worker", version="0.1.0", lifespan=lifespan)

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {settings.token}"
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token")

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        pipeline: Pipeline = request.app.state.pipeline
        return {"status": "ok", "queued": pipeline.queued, "processed": pipeline.processed}

    @app.post(
        "/v1/utterances",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_token)],
    )
    async def post_utterance(
        request: Request,
        session_id: Annotated[str, Query(min_length=1, max_length=64)],
        speaker: Annotated[str, Query(min_length=1, max_length=100)],
        t_start: float,
        speaker_id: Annotated[str | None, Query(max_length=64)] = None,
        character: Annotated[str | None, Query(max_length=100)] = None,
    ) -> dict:
        """Queue one utterance (a WAV body) for transcription; the result goes to the backend."""
        wav = await request.body()
        try:
            duration = wav_duration(wav)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Body must be a PCM WAV file"
            ) from None
        if duration < settings.min_seconds:
            return {"queued": False, "reason": "too short"}
        try:
            request.app.state.pipeline.submit(
                Utterance(
                    session_id, speaker, speaker_id, character or None, t_start, duration, wav
                )
            )
        except QueueFull:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Queue full") from None
        return {"queued": True}

    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory app.main:app_factory``."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
