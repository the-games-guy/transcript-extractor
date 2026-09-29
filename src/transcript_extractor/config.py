"""Settings loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-opus-5-5"


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    anthropic_model: str = DEFAULT_MODEL
    youtube_api_key: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    email_from: str | None = None
    email_to: list[str] = field(default_factory=list)

    transcripts_dir: Path = Path("transcripts")
    state_file: Path = Path(".state/processed.json")
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])

    @classmethod
    def from_env(cls, env_file: str | os.PathLike | None = None) -> "Settings":
        load_dotenv(env_file, override=False)
        env = os.environ
        return cls(
            anthropic_model=env.get("ANTHROPIC_MODEL") or DEFAULT_MODEL,
            youtube_api_key=env.get("YOUTUBE_API_KEY") or None,
            smtp_host=env.get("SMTP_HOST") or None,
            smtp_port=int(env.get("SMTP_PORT") or 587),
            smtp_username=env.get("SMTP_USERNAME") or None,
            smtp_password=env.get("SMTP_PASSWORD") or None,
            smtp_use_tls=_bool(env.get("SMTP_USE_TLS"), True),
            email_from=env.get("EMAIL_FROM") or env.get("SMTP_USERNAME") or None,
            email_to=_split_csv(env.get("EMAIL_TO")),
            transcripts_dir=Path(env.get("TRANSCRIPTS_DIR") or "transcripts"),
            state_file=Path(env.get("STATE_FILE") or ".state/processed.json"),
            transcript_languages=_split_csv(env.get("TRANSCRIPT_LANGUAGES")) or ["en"],
        )

    def require_youtube_key(self) -> str:
        if not self.youtube_api_key:
            raise ConfigError(
                "YOUTUBE_API_KEY is not set. Keyword search uses the YouTube Data API v3; "
                "see .env.example for how to get a key."
            )
        return self.youtube_api_key

    def require_email(self) -> None:
        missing = [
            name
            for name, value in [
                ("SMTP_HOST", self.smtp_host),
                ("EMAIL_FROM", self.email_from),
                ("EMAIL_TO", self.email_to),
            ]
            if not value
        ]
        if missing:
            raise ConfigError(f"Email is not configured; missing: {', '.join(missing)}")
