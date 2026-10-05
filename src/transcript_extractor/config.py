"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


@dataclass
class Settings:
    # Folder notes are written into (/output in Docker; /srv/yt-transcripts on CT102).
    output_dir: Path = Path("/output")
    # Where that folder appears inside the Obsidian vault, e.g. "YouTube Transcripts".
    # Only used for "open in Obsidian" links and the Claude routine prompt.
    vault_folder: str = "YouTube Transcripts"
    obsidian_vault_name: str = ""
    data_dir: Path = Path("/data")
    youtube_api_key: str | None = None
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])
    note_tags: list[str] = field(default_factory=lambda: ["youtube", "transcript"])
    timezone: str = "UTC"
    # Failed / no-transcript entries are removed from the list after this many
    # days without a retry. 0 keeps them forever.
    failed_retention_days: int = 30
    app_username: str = "admin"
    app_password: str | None = None
    secret_key: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None) -> "Settings":
        # Look for .env in the working directory (not next to the installed package),
        # so it works from a service manager or a venv outside the project folder.
        load_dotenv(env_file or find_dotenv(usecwd=True), override=False)
        env = os.environ
        return cls(
            output_dir=Path(env.get("OUTPUT_DIR") or "/output"),
            vault_folder=(env.get("VAULT_FOLDER") or "").strip("/"),
            obsidian_vault_name=env.get("OBSIDIAN_VAULT_NAME") or "",
            data_dir=Path(env.get("DATA_DIR") or "/data"),
            youtube_api_key=env.get("YOUTUBE_API_KEY") or None,
            transcript_languages=_split_csv(env.get("TRANSCRIPT_LANGUAGES")) or ["en"],
            note_tags=_split_csv(env.get("NOTE_TAGS")) or ["youtube", "transcript"],
            timezone=env.get("APP_TIMEZONE") or env.get("TZ") or "UTC",
            failed_retention_days=int(env.get("FAILED_RETENTION_DAYS") or 30),
            app_username=env.get("APP_USERNAME") or "admin",
            app_password=env.get("APP_PASSWORD") or None,
            secret_key=env.get("SECRET_KEY") or None,
            host=env.get("HOST") or "127.0.0.1",
            port=int(env.get("PORT") or 8000),
        )

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
