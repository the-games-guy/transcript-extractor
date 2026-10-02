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


@pytest.mark.parametrize("value,expected", [
    ("UCuAXFkgsw1L7xaCfnd5JJOw", ("id", "UCuAXFkgsw1L7xaCfnd5JJOw")),
    ("@veritasium", ("handle", "@veritasium")),
    ("https://www.youtube.com/@veritasium/videos", ("handle", "@veritasium")),
    ("youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw", ("id", "UCuAXFkgsw1L7xaCfnd5JJOw")),
    ("https://m.youtube.com/user/RickAstleyVEVO", ("username", "RickAstleyVEVO")),
    ("https://www.youtube.com/c/LinusTechTips", ("name", "LinusTechTips")),
    ("Linus Tech Tips", ("name", "Linus Tech Tips")),
])
def test_parse_channel_ref(value, expected):
    assert youtube.parse_channel_ref(value) == expected


def test_resolve_channel_by_handle_costs_one_lookup(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url.rsplit("/", 1)[-1], params))
        return FakeResponse(200, {"items": [{"id": "UCuAXFkgsw1L7xaCfnd5JJOw", "snippet": {
            "title": "Veritasium", "customUrl": "@veritasium"}}]})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    ch = youtube.resolve_channel("https://youtube.com/@veritasium", "KEY")
    assert (ch.channel_id, ch.title, ch.handle) == ("UCuAXFkgsw1L7xaCfnd5JJOw", "Veritasium",
                                                    "@veritasium")
    assert calls == [("channels", {"part": "snippet", "forHandle": "@veritasium", "key": "KEY"})]


def test_resolve_channel_name_falls_back_to_search(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append(endpoint)
        if endpoint == "search":
            return FakeResponse(200, {"items": [{"id": {"channelId": "UC" + "x" * 22},
                                                 "snippet": {"title": "Linus Tech Tips"}}]})
        return FakeResponse(200, {"items": []})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    ch = youtube.resolve_channel("Linus Tech Tips", "KEY")
    assert ch.channel_id == "UC" + "x" * 22 and ch.title == "Linus Tech Tips"
    assert calls == ["search"]  # spaces can't be a handle, so no handle lookup


def test_resolve_channel_not_found(monkeypatch):
    monkeypatch.setattr(youtube.requests, "get",
                        lambda *a, **k: FakeResponse(200, {"items": []}))
    with pytest.raises(YouTubeError, match="Couldn't find"):
        youtube.resolve_channel("@nobody-here", "KEY")


def _upload(video_id, published, title="T"):
    return {"snippet": {"title": title, "videoOwnerChannelTitle": "Chan"},
            "contentDetails": {"videoId": video_id, "videoPublishedAt": published}}


def test_channel_uploads_filters_date_and_length(monkeypatch):
    channel_id = "UC" + "a" * 22
    calls = []

    def fake_get(url, params, timeout):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append((endpoint, params))
        if endpoint == "playlistItems":
            return FakeResponse(200, {"items": [
                _upload("newlongvid1", "2026-09-30T10:00:00Z"),
                _upload("newshortvid", "2026-09-29T10:00:00Z"),
                {"snippet": {"title": "Private video"}, "contentDetails": {"videoId": "privatevid1"}},
                _upload("oldlongvid1", "2026-01-01T10:00:00Z"),
            ], "nextPageToken": "more"})
        return FakeResponse(200, {"items": [
            {"id": "newlongvid1", "contentDetails": {"duration": "PT1H2M3S"}},
            {"id": "newshortvid", "contentDetails": {"duration": "PT45S"}},
        ]})

    monkeypatch.setattr(youtube.requests, "get", fake_get)
    videos = youtube.channel_uploads(
        channel_id, "KEY", max_results=10, video_duration="long",
        published_after=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert [(v.video_id, v.channel, v.channel_id) for v in videos] == [
        ("newlongvid1", "Chan", channel_id)]
    # One page (the old video marks the cutoff), plus one length lookup.
    assert [c[0] for c in calls] == ["playlistItems", "videos"]
    assert calls[0][1]["playlistId"] == "UU" + "a" * 22


def test_find_videos_picks_uploads_or_search(monkeypatch):
    used = []
    monkeypatch.setattr(youtube, "channel_uploads", lambda *a, **k: used.append("uploads") or [])
    monkeypatch.setattr(youtube, "search_videos", lambda *a, **k: used.append(("search", k.get("channel_id"))) or [])
    cid = "UC" + "a" * 22
    youtube.find_videos("", "KEY", channel_id=cid, order="date")
    youtube.find_videos("", "KEY", channel_id=cid, order="viewCount")
    youtube.find_videos("tips", "KEY", channel_id=cid, order="date")
    assert used == ["uploads", ("search", cid), ("search", cid)]
    with pytest.raises(YouTubeError):
        youtube.find_videos(" ", "KEY")
