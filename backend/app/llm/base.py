from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_json(self, *, system: str, prompt: str, schema: type[T]) -> T:
        """Return the model's answer parsed into ``schema``. Raises on invalid output."""
        ...
