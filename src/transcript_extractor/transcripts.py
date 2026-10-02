"""Fetching video transcripts."""

from __future__ import annotations

from dataclasses import dataclass

import requests
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    NoTranscriptFound,
    RequestBlocked,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)

# Failures worth retrying later (as opposed to "this video has no captions").
_TRANSIENT = (RequestBlocked, YouTubeRequestFailed)


class TranscriptError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Segment:
    start: float
    duration: float
    text: str


@dataclass
class Transcript:
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


def _from_fetched(fetched) -> Transcript:
    return Transcript(
        video_id=fetched.video_id,
        language=fetched.language,
        language_code=fetched.language_code,
        is_generated=fetched.is_generated,
        segments=[
            Segment(start=s.start, duration=s.duration, text=s.text.replace("\n", " ").strip())
            for s in fetched.snippets
            if s.text.strip()
        ],
    )


def fetch_transcript(
    video_id: str,
    languages: list[str] | None = None,
    api: YouTubeTranscriptApi | None = None,
) -> Transcript:
    """Fetch a transcript, preferring `languages`; falls back to any available one
    (translated into the first preferred language when YouTube allows it)."""
    api = api or YouTubeTranscriptApi()
    languages = languages or ["en"]
    try:
        return _from_fetched(api.fetch(video_id, languages=languages))
    except NoTranscriptFound:
        pass
    except (CouldNotRetrieveTranscript, requests.RequestException) as exc:
        raise _wrap(exc) from exc

    try:
        available = list(api.list(video_id))
        if not available:
            raise TranscriptError(f"No transcripts available for {video_id}")
        chosen = available[0]
        if chosen.is_translatable and any(
            t.language_code == languages[0] for t in chosen.translation_languages
        ):
            chosen = chosen.translate(languages[0])
        return _from_fetched(chosen.fetch())
    except (CouldNotRetrieveTranscript, requests.RequestException) as exc:
        raise _wrap(exc) from exc


def _wrap(exc: Exception) -> TranscriptError:
    permanent = not isinstance(exc, (*_TRANSIENT, requests.RequestException))
    return TranscriptError(_short_reason(exc), permanent=permanent)


def _short_reason(exc: Exception) -> str:
    # youtube-transcript-api errors carry a long help text; keep the most useful line.
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    if isinstance(exc, CouldNotRetrieveTranscript) and len(lines) > 1:
        detail = lines[1] if lines[0].startswith("Could not retrieve") else lines[0]
    else:
        detail = lines[0] if lines else ""
    return f"{type(exc).__name__}: {detail}".strip().rstrip(":")
