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
    channel_id: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class Channel:
    channel_id: str
    title: str = ""
    handle: str = ""

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/channel/{self.channel_id}"


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
        "type": "video",
        "maxResults": max(1, min(int(max_results), 50)),
        "order": order,
    }
    if query:
        params["q"] = query
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
                channel_id=snippet.get("channelId", ""),
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
                    channel_id=s.get("channelId", ""),
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


# --- channels ----------------------------------------------------------------

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
# YouTube's own videoDuration buckets, in seconds.
_DURATION_BUCKETS = {"short": (0, 240), "medium": (240, 1200), "long": (1200, None)}


def parse_channel_ref(value: str) -> tuple[str, str]:
    """Classify what someone typed as a channel.

    Returns (kind, value) where kind is "id" (UC…), "handle" (@name),
    "username" (legacy /user/ URL) or "name" (anything else, looked up by search).
    """
    value = value.strip()
    if _CHANNEL_ID_RE.match(value):
        return "id", value
    if _HANDLE_RE.match(value):
        return "handle", value

    candidate = value if "://" in value else "https://" + value
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    if host in {"youtube.com", "music.youtube.com"} and parts:
        first = parts[0]
        if first.startswith("@") and _HANDLE_RE.match(first):
            return "handle", first
        if first == "channel" and len(parts) > 1 and _CHANNEL_ID_RE.match(parts[1]):
            return "id", parts[1]
        if first == "user" and len(parts) > 1:
            return "username", parts[1]
        if first == "c" and len(parts) > 1:
            return "name", parts[1]
    if not value:
        raise YouTubeError("Enter a channel.")
    return "name", value


def _channel_from_item(item: dict) -> Channel:
    snippet = item.get("snippet", {})
    channel_id = item.get("id")
    if isinstance(channel_id, dict):  # search results nest the ID
        channel_id = channel_id.get("channelId")
    return Channel(channel_id=channel_id or snippet.get("channelId", ""),
                   title=snippet.get("title") or snippet.get("channelTitle", ""),
                   handle=snippet.get("customUrl", ""))


def resolve_channel(value: str, api_key: str) -> Channel:
    """Turn a channel ID, @handle, channel URL or name into a Channel.

    IDs, handles and URLs cost 1 API unit. A bare name is tried as a handle
    first, then falls back to a channel search (100 units).
    """
    kind, ref = parse_channel_ref(value)
    lookups = {
        "id": [{"id": ref}],
        "handle": [{"forHandle": ref}],
        "username": [{"forUsername": ref}, {"forHandle": "@" + ref}],
        "name": [{"forHandle": "@" + ref}] if re.fullmatch(r"[A-Za-z0-9._-]{3,30}", ref) else [],
    }[kind]
    for params in lookups:
        items = _api_get("channels", api_key, {"part": "snippet", **params}).get("items", [])
        if items:
            return _channel_from_item(items[0])
    if kind == "name":
        items = _api_get("search", api_key, {"part": "snippet", "type": "channel",
                                             "q": ref, "maxResults": 1}).get("items", [])
        if items:
            return _channel_from_item(items[0])
    raise YouTubeError(f"Couldn't find a YouTube channel for {value!r}.")


def _duration_seconds(iso: str) -> int | None:
    m = _DURATION_RE.match(iso or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _matches_duration(seconds: int | None, bucket: str) -> bool:
    if seconds is None:
        return False
    lo, hi = _DURATION_BUCKETS[bucket]
    return seconds >= lo and (hi is None or seconds < hi)


def channel_uploads(
    channel_id: str,
    api_key: str,
    *,
    max_results: int = 10,
    published_after: datetime | None = None,
    video_duration: str | None = None,
    max_pages: int = 4,
) -> list[Video]:
    """A channel's newest uploads, via its uploads playlist.

    Costs 1 API unit per page (plus 1 per page when filtering by length),
    against 100 for a search.
    """
    if not _CHANNEL_ID_RE.match(channel_id):
        raise YouTubeError(f"Not a channel ID: {channel_id!r}")
    max_results = max(1, min(int(max_results), 50))
    bucket = video_duration if video_duration in _DURATION_BUCKETS else None
    if published_after and published_after.tzinfo is None:
        published_after = published_after.replace(tzinfo=timezone.utc)

    videos: list[Video] = []
    page_token = None
    for _ in range(max_pages):
        params = {"part": "snippet,contentDetails", "playlistId": "UU" + channel_id[2:],
                  "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        data = _api_get("playlistItems", api_key, params)
        page, reached_cutoff = [], False
        for item in data.get("items", []):
            snippet, details = item.get("snippet", {}), item.get("contentDetails", {})
            video_id = details.get("videoId") or snippet.get("resourceId", {}).get("videoId")
            published = details.get("videoPublishedAt")
            if not video_id or not published:  # private or deleted
                continue
            if published_after and datetime.fromisoformat(
                published.replace("Z", "+00:00")
            ) < published_after:
                reached_cutoff = True
                continue
            page.append(Video(
                video_id=video_id, title=snippet.get("title", ""),
                channel=snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle", ""),
                published_at=published, description=snippet.get("description", ""),
                channel_id=channel_id,
            ))
        if bucket and page:
            lengths = _api_get("videos", api_key, {
                "part": "contentDetails", "id": ",".join(v.video_id for v in page)})
            seconds = {i["id"]: _duration_seconds(i.get("contentDetails", {}).get("duration"))
                       for i in lengths.get("items", [])}
            page = [v for v in page if _matches_duration(seconds.get(v.video_id), bucket)]
        videos.extend(page)
        page_token = data.get("nextPageToken")
        if len(videos) >= max_results or reached_cutoff or not page_token:
            break
    videos.sort(key=lambda v: v.published_at, reverse=True)
    return videos[:max_results]


def find_videos(
    query: str,
    api_key: str,
    *,
    channel_id: str | None = None,
    order: str = "relevance",
    **kwargs,
) -> list[Video]:
    """Keyword search, optionally limited to a channel.

    A channel with no keywords sorted by newest reads the channel's uploads
    instead of searching, which is cheaper and doesn't miss videos.
    """
    if channel_id and not query.strip() and order == "date":
        return channel_uploads(channel_id, api_key, **kwargs)
    if not query.strip() and not channel_id:
        raise YouTubeError("Enter keywords, a channel, or both.")
    return search_videos(query, api_key, channel_id=channel_id, order=order, **kwargs)
