from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables prefixed with ``VTT_``."""

    model_config = SettingsConfigDict(env_prefix="VTT_", env_file=".env", extra="ignore")

    rules_db_path: Path = Path("data/pf2e_remaster.db")

    # Tokens issued to clients (one per Foundry world / Discord guild), comma separated.
    client_tokens: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Browser origins allowed to call the API (the Molten-hosted Foundry URL).
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    llm_provider: Literal["gemini", "fake"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Language used for interpretations and recaps. Rules text is always quoted in English.
    answer_language: str = "Finnish"

    max_retrieved_entries: int = 8

    # How often to check Archives of Nethys for a new data build (hours). 0 disables.
    rules_refresh_hours: float = 24

    @field_validator("client_tokens", "cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
