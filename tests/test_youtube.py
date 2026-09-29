from datetime import datetime, timezone

import pytest

from transcript_extractor import youtube
from transcript_extractor.youtube import YouTubeError, parse_video_id


@pytest.mark.parametrize(
    "url",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=42s",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ?feature=share",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RD",
    ],
)
def test_parse_video_id(url):
    assert parse_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "bad", ["", "https://example.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/@channel",
            "https://youtube.com/watch?v=short"]
)
def test_parse_video_id_rejects(bad):
    with pytest.raises(YouTubeError):
        parse_video_id(bad)


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_search_videos_builds_request(monkeypatch):
    calls = {}

    def fake_get(url, params, timeout):
        calls["url"], calls["params"] = url, params
        return FakeResponse(200, {"items": [
            {"id": {"videoId": "abcdefghijk"},
             "snippet": {"title": "T", "channelTitle": "C", "publishedAt": "2026-01-01T00:00:00Z"}},
            {"id": {"channelId": "UCxyz"}, "snippet": {}},  # non-video results are skipped
        ]})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    videos = youtube.search_videos(
        "python", "KEY", max_results=99, order="date",
        published_after=datetime(2026, 1, 1, tzinfo=timezone.utc), video_duration="long",
    )
    assert [v.video_id for v in videos] == ["abcdefghijk"]
    assert videos[0].url == "https://www.youtube.com/watch?v=abcdefghijk"
    p = calls["params"]
    assert calls["url"].endswith("/search")
    assert p["maxResults"] == 50 and p["type"] == "video" and p["key"] == "KEY"
    assert p["publishedAfter"] == "2026-01-01T00:00:00Z"
    assert p["videoDuration"] == "long"


def test_search_videos_api_error(monkeypatch):
    monkeypatch.setattr(
        youtube.requests, "get",
        lambda *a, **k: FakeResponse(403, {"error": {"message": "quota exceeded"}}),
    )
    with pytest.raises(YouTubeError, match="quota exceeded"):
        youtube.search_videos("x", "KEY")


def test_get_video_falls_back_to_oembed(monkeypatch):
    def fake_get(url, params, timeout):
        if url == youtube.OEMBED_URL:
            return FakeResponse(200, {"title": "Hello", "author_name": "Chan"})
        return FakeResponse(200, {"items": []})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    v = youtube.get_video("abcdefghijk", api_key="KEY")
    assert (v.title, v.channel) == ("Hello", "Chan")
