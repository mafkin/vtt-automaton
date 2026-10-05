from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables prefixed with ``STT_``."""

    model_config = SettingsConfigDict(env_prefix="STT_", env_file=".env", extra="ignore")

    # Shared secret the Discord bot sends with each utterance.
    token: str = ""

    # Where finished segments go, and the backend client token to use.
    backend_url: str = "http://backend:8765"
    backend_token: str = ""

    model: str = "large-v3"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "default"  # float16 on GPU, int8 on CPU
    language: str = "fi"
    beam_size: int = 5

    # Words Whisper should expect: the wake word, character and place names, English game terms.
    prompt_terms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "Nethys",
            "Pathfinder",
            "Strike",
            "Stride",
            "Step",
            "Trip",
            "Grapple",
            "Shove",
            "Demoralize",
            "Reactive Strike",
            "Shield Block",
            "off-guard",
            "prone",
            "frightened",
            "flanking",
            "hit points",
            "AC",
            "DC",
        ]
    )

    min_seconds: float = 0.4  # shorter clips are coughs and clicks
    max_queue: int = 200

    @field_validator("prompt_terms", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
