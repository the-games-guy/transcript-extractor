"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass
class Settings:
    vault_dir: Path = Path("vault")
    notes_folder: str = "YouTube"
    data_dir: Path = Path("data")
    youtube_api_key: str | None = None
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])
    note_tags: list[str] = field(default_factory=lambda: ["youtube", "transcript"])
    timezone: str = "UTC"
    obsidian_vault_name: str = ""
    app_username: str = "admin"
    app_password: str | None = None
    secret_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None) -> "Settings":
        load_dotenv(env_file, override=False)
        env = os.environ
        vault_dir = Path(env.get("VAULT_DIR") or "vault")
        return cls(
            vault_dir=vault_dir,
            notes_folder=(env.get("NOTES_FOLDER") or "YouTube").strip("/"),
            data_dir=Path(env.get("DATA_DIR") or "data"),
            youtube_api_key=env.get("YOUTUBE_API_KEY") or None,
            transcript_languages=_split_csv(env.get("TRANSCRIPT_LANGUAGES")) or ["en"],
            note_tags=_split_csv(env.get("NOTE_TAGS")) or ["youtube", "transcript"],
            timezone=env.get("APP_TIMEZONE") or "UTC",
            obsidian_vault_name=env.get("OBSIDIAN_VAULT_NAME") or vault_dir.resolve().name,
            app_username=env.get("APP_USERNAME") or "admin",
            app_password=env.get("APP_PASSWORD") or None,
            secret_key=env.get("SECRET_KEY") or None,
            host=env.get("HOST") or "127.0.0.1",
            port=int(env.get("PORT") or 8000),
        )

    @property
    def notes_dir(self) -> Path:
        return self.vault_dir / self.notes_folder if self.notes_folder else self.vault_dir

    @property
    def db_path(self) -> Path:
        return self.data_dir / "transcript-extractor.sqlite3"

    def require_youtube_key(self) -> str:
        if not self.youtube_api_key:
            raise ConfigError(
                "YOUTUBE_API_KEY is not set. Keyword search and scheduled lookups use the "
                "YouTube Data API v3; see .env.example for how to get a key."
            )
        return self.youtube_api_key
