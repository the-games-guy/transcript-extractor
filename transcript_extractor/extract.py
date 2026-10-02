"""Turn one YouTube URL into a note. Shared by the CLI and the web UI."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApiException

from . import markdown, writer, youtube


@dataclass
class Result:
    status: str  # "wrote", "skip" or "error"
    source: str  # what the user typed
    message: str  # the note's file name, or the error


def check_output_dir(output_dir: Path) -> str | None:
    """Return why notes can't be written to output_dir, or None if they can."""
    if not output_dir.is_dir():
        return f"output folder {output_dir} does not exist"
    if not os.access(output_dir, os.W_OK):
        return (
            f"cannot write to {output_dir} as uid {os.getuid()}; "
            "check APP_UID/APP_GID in .env match the syncthing user"
        )
    return None


def extract(raw: str, output_dir: Path, languages: list[str], force: bool) -> Result:
    try:
        video_id = youtube.parse_video_id(raw)
        existing = writer.find_existing(output_dir, video_id)
        if existing and not force:
            return Result("skip", raw, existing.name)
        video = youtube.fetch_video(video_id)
        transcript = youtube.fetch_transcript(video_id, languages)
        note = markdown.render(video, transcript, date.today())
        path = output_dir / writer.safe_filename(video.title, video_id)
        writer.write_atomic(path, note)
        # A renamed video would otherwise leave the old note behind.
        if existing and existing != path:
            existing.unlink()
        return Result("wrote", raw, path.name)
    except (ValueError, OSError, YouTubeTranscriptApiException) as exc:
        return Result("error", raw, str(exc))


def parse_languages(value: str) -> list[str]:
    return [lang.strip() for lang in value.split(",") if lang.strip()]
