from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration from environment variables (no prefix), e.g. GEMINI_API_KEY."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    backend_url: str = "http://backend:8765"
    comfyui_url: str = "http://comfyui:8188"
    docker_proxy_url: str = "http://docker-proxy:2375"

    # The speech-to-text container that holds the GPU between sessions. compose.yaml gives it
    # this fixed name. Empty = no handover (transcription not installed).
    stt_container_name: str = "vtt-stt-worker"
    # ComfyUI only runs during a comic: the worker starts it and stops it again, which is what
    # releases its VRAM. compose.yaml gives it this fixed name.
    comfyui_container_name: str = "vtt-comfyui"
    # Seconds to wait for ComfyUI to answer after starting its container.
    comfyui_start_timeout: float = 180

    # Exists while a comic holds the GPU. On the shared data volume, so the backend can refuse
    # to start a session (no transcription) while it is there.
    lock_path: str = "/data/comic.lock"

    # Compose project whose containers the dashboard lists (compose.yaml: name).
    compose_project: str = "vtt-automaton"

    # The comic bible (bible.json, characters/<id>/ reference images); see app/bible.py.
    bible_dir: str = "/data/comic"
    # IP-Adapter: how strongly a panel follows a character's reference image (0-1), and the
    # fraction of sampling steps it acts on. Higher copies more of the reference picture.
    ipadapter_weight: float = 0.5
    ipadapter_end_at: float = 0.8

    sessions_db_path: str = "/data/sessions.db"
    comfy_output_dir: str = "/comfy_output"
    jobs_dir: str = "/data/jobs"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    # A whole comic (8-10 pages x 3-5 panels, tens of seconds per panel) takes well over an hour.
    job_timeout_seconds: int = 4 * 3600


settings = Settings()
