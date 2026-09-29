"""YouTube URL parsing, keyword search, and video metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"
OEMBED_URL = "https://www.youtube.com/oembed"
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_TIMEOUT = 20


class YouTubeError(RuntimeError):
    pass


@dataclass
class Video:
    video_id: str
    title: str = ""
    channel: str = ""
    published_at: str = ""
    description: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def parse_video_id(url_or_id: str) -> str:
    """Extract the 11-character video ID from any common YouTube URL form."""
    value = url_or_id.strip()
    if _VIDEO_ID_RE.match(value):
        return value

    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    path_parts = [p for p in parsed.path.split("/") if p]

    candidate = None
    if host == "youtu.be" and path_parts:
        candidate = path_parts[0]
    elif host in {"youtube.com", "music.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live", "v", "e"}:
            candidate = path_parts[1]

    if candidate and _VIDEO_ID_RE.match(candidate):
        return candidate
    raise YouTubeError(f"Could not find a YouTube video ID in: {url_or_id!r}")


def _api_get(endpoint: str, api_key: str, params: dict) -> dict:
    resp = requests.get(
        f"{API_BASE}/{endpoint}", params={**params, "key": api_key}, timeout=_TIMEOUT
    )
    if resp.status_code != 200:
        try:
            message = resp.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = resp.text[:300]
        raise YouTubeError(f"YouTube API {endpoint} failed ({resp.status_code}): {message}")
    return resp.json()


def search_videos(
    query: str,
    api_key: str,
    *,
    max_results: int = 10,
    published_after: datetime | None = None,
    order: str = "relevance",
    channel_id: str | None = None,
    video_duration: str | None = None,
) -> list[Video]:
    """Search YouTube for videos matching a keyword query."""
    params: dict = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(1, min(int(max_results), 50)),
        "order": order,
    }
    if published_after:
        if published_after.tzinfo is None:
            published_after = published_after.replace(tzinfo=timezone.utc)
        params["publishedAfter"] = (
            published_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
    if channel_id:
        params["channelId"] = channel_id
    if video_duration and video_duration != "any":
        params["videoDuration"] = video_duration

    data = _api_get("search", api_key, params)
    videos = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet", {})
        videos.append(
            Video(
                video_id=video_id,
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
                published_at=snippet.get("publishedAt", ""),
                description=snippet.get("description", ""),
            )
        )
    return videos


def get_video(video_id: str, api_key: str | None = None) -> Video:
    """Look up title/channel for a video.

    Uses the Data API when a key is available (richer metadata), otherwise the
    keyless oEmbed endpoint. Falls back to a bare Video if both fail.
    """
    if api_key:
        try:
            data = _api_get("videos", api_key, {"part": "snippet", "id": video_id})
            items = data.get("items", [])
            if items:
                s = items[0]["snippet"]
                return Video(
                    video_id=video_id,
                    title=s.get("title", ""),
                    channel=s.get("channelTitle", ""),
                    published_at=s.get("publishedAt", ""),
                    description=s.get("description", ""),
                )
        except (YouTubeError, requests.RequestException):
            pass

    try:
        resp = requests.get(
            OEMBED_URL,
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            return Video(
                video_id=video_id,
                title=data.get("title", ""),
                channel=data.get("author_name", ""),
            )
    except (requests.RequestException, ValueError):
        pass
    return Video(video_id=video_id)
