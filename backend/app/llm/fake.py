from collections.abc import Callable

from pydantic import BaseModel

from app.llm.base import T

Responder = Callable[[str, str, type[BaseModel]], BaseModel]


class FakeProvider:
    """Deterministic provider for tests and offline development.

    ``responder`` receives (system, prompt, schema) and returns an instance of ``schema``.
    Every call is recorded in ``calls``.
    """

    def __init__(self, responder: Responder) -> None:
        self._responder = responder
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    async def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        self.calls.append((system, prompt, schema))
        result = self._responder(system, prompt, schema)
        return schema.model_validate(result.model_dump())
