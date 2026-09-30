"""Look up a video's metadata and transcript."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from youtube_transcript_api import YouTubeTranscriptApi

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass
class Video:
    video_id: str
    title: str
    channel: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class Snippet:
    start: float
    text: str


@dataclass
class Transcript:
    language: str
    language_code: str
    is_generated: bool
    snippets: list[Snippet]


def parse_video_id(value: str) -> str:
    """Accept a bare ID or any common YouTube URL form."""
    value = value.strip()
    if _ID.match(value):
        return value
    parsed = urllib.parse.urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        query = urllib.parse.parse_qs(parsed.query)
        if "v" in query:
            candidate = query["v"][0]
        else:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed", "v"):
                candidate = parts[1]
    if _ID.match(candidate):
        return candidate
    raise ValueError(f"not a YouTube video URL or ID: {value!r}")


def fetch_video(video_id: str, timeout: float = 15) -> Video:
    """Get the title and channel from YouTube's oEmbed endpoint (no API key)."""
    watch = f"https://www.youtube.com/watch?v={video_id}"
    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": watch, "format": "json"}
    )
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = json.load(resp)
    return Video(
        video_id=video_id,
        title=data.get("title") or video_id,
        channel=data.get("author_name") or "",
    )


def fetch_transcript(video_id: str, languages: list[str]) -> Transcript:
    fetched = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    return Transcript(
        language=fetched.language,
        language_code=fetched.language_code,
        is_generated=fetched.is_generated,
        snippets=[Snippet(start=s.start, text=s.text) for s in fetched.snippets],
    )
