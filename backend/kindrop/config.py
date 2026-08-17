from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KINDROP_", case_sensitive=False)

    database_url: str = "sqlite:////data/kindrop.db"
    cache_root: Path = Path("/cache")
    secret_key_file: Path = Path("/run/secrets/kindrop.key")
    app_base_url: str = "http://127.0.0.1:8787"
    frontend_dist: Path = Path("/app/frontend")
    worker_poll_seconds: float = 2.0
    mail_poll_seconds: float = 60.0
