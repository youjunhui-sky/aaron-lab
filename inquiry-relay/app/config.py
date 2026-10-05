"""Application settings — environment variables only, no secrets in code."""

import json
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

    # --- anti-spam (D2) ---
    # Cloudflare Turnstile: empty secret = disabled (default off for local dev)
    turnstile_secret_key: str = ""
    # incoming field that must stay empty; filled => bot
    honeypot_field: str = "website"
    # max accepted-or-rejected submissions per window, per IP and per email
    rate_limit_max: int = 5
    rate_limit_window_seconds: int = 60
    # same email + same message within this window => duplicate
    dedupe_window_seconds: int = 86400

    # --- field mapping (D2) ---
    # JSON: canonical -> incoming field name, e.g. {"email": "mail", "message": "body"}
    # empty = canonical names only; canonical names still work as fallback for unmapped keys
    field_map: str = ""

    def get_field_map(self) -> dict[str, str]:
        if not self.field_map.strip():
            return {}
        try:
            parsed = json.loads(self.field_map)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k): str(v) for k, v in parsed.items() if v}


@lru_cache
def get_settings() -> Settings:
    return Settings()
