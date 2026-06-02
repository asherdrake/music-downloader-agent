from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    llm_provider: Literal["anthropic", "openai", "google"] = "anthropic"
    llm_model: str = ""

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    discogs_api_token: str = ""

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8000/callback"

    local_files_directory: Path = Path.home() / "Music" / "Local"
    local_bridge_playlist_id: str = ""

    checkpoint_db_path: Path = Path.home() / ".music-downloader" / "checkpoints.db"

    confidence_threshold: float = 0.7

    @field_validator("confidence_threshold")
    @classmethod
    def validate_confidence_threshold(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("CONFIDENCE_THRESHOLD must be between 0.0 and 1.0")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
