import logging

from google import genai
from google.genai import errors, types

from app.llm.base import T

log = logging.getLogger(__name__)


class GeminiProvider:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2) -> None:
        if not api_key:
            raise ValueError("VTT_GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature
        # Turned off if the configured model rejects a thinking level.
        self._fast_supported = True

    def _config(self, system: str, schema: type[T], fast: bool) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._temperature,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=(
                types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL) if fast else None
            ),
        )

    async def generate_json(
        self, *, system: str, prompt: str, schema: type[T], fast: bool = False
    ) -> T:
        fast = fast and self._fast_supported
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=prompt, config=self._config(system, schema, fast)
            )
        except errors.ClientError as exc:
            if not fast:
                raise
            # Not every model takes a thinking level; fall back to its default once and remember.
            log.warning(
                "Model %s rejected a minimal thinking level (%s); using its default",
                self._model,
                exc,
            )
            self._fast_supported = False
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=prompt, config=self._config(system, schema, False)
            )
        return schema.model_validate_json(response.text or "")
