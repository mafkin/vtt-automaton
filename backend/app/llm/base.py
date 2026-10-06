from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_json(
        self, *, system: str, prompt: str, schema: type[T], fast: bool = False
    ) -> T:
        """Return the model's answer parsed into ``schema``. Raises on invalid output.

        ``fast`` asks for the lowest-latency mode (minimal reasoning) for simple steps.
        """
        ...
