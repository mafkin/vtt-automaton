import pytest
from google.genai import errors, types
from pydantic import BaseModel

from app.llm.gemini import GeminiProvider


class Answer(BaseModel):
    value: str


class FakeModels:
    def __init__(self, reject_thinking: bool):
        self.reject_thinking = reject_thinking
        self.configs: list[types.GenerateContentConfig] = []

    async def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        if self.reject_thinking and config.thinking_config is not None:
            raise errors.ClientError(400, {"error": {"message": "thinking_level not supported"}})
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(parts=[types.Part(text='{"value": "ok"}')]))
            ]
        )


def provider(reject_thinking=False):
    p = GeminiProvider(api_key="k", model="gemini-test")
    models = FakeModels(reject_thinking)
    p._client = type("C", (), {"aio": type("A", (), {"models": models})()})()
    return p, models


async def test_fast_uses_minimal_thinking():
    p, models = provider()
    assert (await p.generate_json(system="s", prompt="p", schema=Answer, fast=True)).value == "ok"
    assert models.configs[0].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    await p.generate_json(system="s", prompt="p", schema=Answer)
    assert models.configs[1].thinking_config is None


async def test_model_without_thinking_levels_falls_back_once():
    p, models = provider(reject_thinking=True)
    assert (await p.generate_json(system="s", prompt="p", schema=Answer, fast=True)).value == "ok"
    await p.generate_json(system="s", prompt="p", schema=Answer, fast=True)
    # first call: rejected + retried; second call: no thinking config at all
    assert [c.thinking_config is not None for c in models.configs] == [True, False, False]


async def test_errors_without_fast_mode_are_raised():
    p, models = provider()

    async def broken(**_):
        raise errors.ClientError(400, {"error": {"message": "API key not valid"}})

    models.generate_content = broken
    with pytest.raises(errors.ClientError):
        await p.generate_json(system="s", prompt="p", schema=Answer)
    assert p._fast_supported  # only a rejected thinking level turns fast mode off
