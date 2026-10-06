from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = "redis://redis:6379/0"
    backend_url: str = "http://backend:8765"
    comfyui_url: str = "http://comfyui:8188"
    docker_proxy_url: str = "http://docker-proxy:2375"
    stt_container_name: str = "vtt-automaton-stt-worker-1"
    jobs_dir: str = "/data/jobs"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-pro-high"
    
settings = Settings()
