import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import foundry_ws, rulings, voice
from app.config import Settings, get_settings
from app.foundry.hub import FoundryHub
from app.ingest.scheduler import refresh_rules_periodically
from app.llm.base import LLMProvider
from app.rules.service import RulesService
from app.rules.store import RulesStore
from app.voice.service import VoiceRulesService


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "gemini":
        from app.llm.gemini import GeminiProvider

        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    raise ValueError(f"LLM provider {settings.llm_provider!r} must be injected explicitly")


def create_app(settings: Settings | None = None, llm: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.client_tokens:
        raise RuntimeError("VTT_CLIENT_TOKENS must contain at least one token")

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if settings.rules_refresh_hours > 0:
            task = asyncio.create_task(
                refresh_rules_periodically(settings.rules_db_path, settings.rules_refresh_hours)
            )
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="VTT Automaton", version="0.1.0", lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.settings = settings
    app.state.answer_language = settings.answer_language
    app.state.rules_service = RulesService(
        RulesStore(settings.rules_db_path),
        llm or build_llm(settings),
        answer_language=settings.answer_language,
        max_entries=settings.max_retrieved_entries,
        max_chars=settings.max_ruling_chars,
    )
    app.state.foundry_hub = FoundryHub()
    app.state.voice_service = VoiceRulesService(
        app.state.rules_service,
        app.state.foundry_hub,
        language=settings.answer_language,
        wake_words=settings.wake_words,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(rulings.router)
    app.include_router(voice.router)
    app.include_router(foundry_ws.router)
    return app


def app_factory() -> FastAPI:
    """Entry point for ``uvicorn --factory app.main:app_factory``."""
    logging.basicConfig(level=logging.INFO)
    return create_app()
