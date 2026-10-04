from google import genai
from google.genai import types

from app.llm.base import T


class GeminiProvider:
    def __init__(self, api_key: str, model: str, temperature: float = 0.2) -> None:
        if not api_key:
            raise ValueError("VTT_GEMINI_API_KEY is not set")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._temperature = temperature

    async def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self._temperature,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        return schema.model_validate_json(response.text or "")
