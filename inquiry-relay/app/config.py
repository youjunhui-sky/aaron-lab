"""Application settings — environment variables only, no secrets in code."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INQUIRY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = Path("data/inquiries.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
