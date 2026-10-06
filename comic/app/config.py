from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables (no prefix), e.g. GEMINI_API_KEY."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    # Read-only Docker socket proxy: the dashboard's container list.
    docker_proxy_url: str = "http://docker-proxy:2375"
    # Compose project whose containers the dashboard lists (compose.yaml: name).
    compose_project: str = "vtt-automaton"

    sessions_db_path: str = "/data/sessions.db"
    # The comic bible (bible.json, characters/<id>/ reference images), the comics being made
    # (comics/<id>/) and the token budget (limits.json); see app/bible.py and app/comics.py.
    bible_dir: str = "/data/comic"

    gemini_api_key: str = ""
    # Extracts events, writes scripts, reads lettering back.
    gemini_model: str = "gemini-3.8-flash"
    # Draws the comic pages (with the characters' reference images).
    gemini_image_model: str = "gemini-3-pro-image"

    # One comic step may take this long; drawing 10 pages with redraws is ~10 minutes.
    job_timeout_seconds: int = 3600


settings = Settings()
