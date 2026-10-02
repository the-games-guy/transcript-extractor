import base64

import pytest

from transcript_extractor import web
from transcript_extractor.youtube import Channel, Video


class FakeWorker:
    def __init__(self):
        self.enqueued, self.scheduled, self.ran = [], [], []

    def enqueue(self, videos, *, source, watch_id=None, force=False):
        self.enqueued.append(([v.video_id for v in videos], source, force))
        return len(videos), 0

    def schedule(self, watch):
        self.scheduled.append(dict(watch))

    def unschedule(self, watch_id):
        self.scheduled.append(("removed", watch_id))

    def next_run(self, watch_id):
        return None

    def is_running(self, watch_id):
        return False

    def run_watch_now(self, watch_id):
        self.ran.append(watch_id)


@pytest.fixture
def worker():
    return FakeWorker()


@pytest.fixture
def client(settings, worker):
    app = web.create_app(settings, start_worker=False, worker=worker)
    app.config["TESTING"] = True
    return app.test_client()


def test_pages_render(client):
    for path in ["/", "/search", "/watches", "/watches/new", "/routine", "/healthz"]:
        assert client.get(path).status_code == 200, path


def test_add_videos(client, worker):
    resp = client.post("/videos", data={"urls": "https://youtu.be/dQw4w9WgXcQ\nnot-a-link"},
                       follow_redirects=True)
    assert worker.enqueued == [(["dQw4w9WgXcQ"], "manual", True)]
    body = resp.get_data(as_text=True)
    assert "Not a YouTube video link: not-a-link" in body
    assert "Fetching 1 transcript" in body


def test_index_lists_videos_with_obsidian_link(client):
    db = client.application.config["DB"]
    db.upsert_video(Video("dQw4w9WgXcQ", "Song", "Rick"), status="saved",
                    note_path="Song (dQw4w9WgXcQ).md", source="manual")
    db.upsert_video(Video("zzzzzzzzzzz", "Broken"), status="failed", error="IpBlocked: nope")
    body = client.get("/").get_data(as_text=True)
    assert ("obsidian://open?vault=tokvault&amp;file=YouTube%20Transcripts/"
            "Song%20%28dQw4w9WgXcQ%29") in body
    assert "IpBlocked: nope" in body and "Retry" in body
    assert "Broken" not in client.get("/?status=saved").get_data(as_text=True)


def test_retry_resets_attempts(client, worker):
    db = client.application.config["DB"]
    db.upsert_video(Video("zzzzzzzzzzz", "Broken"), status="failed", count_attempt=True)
    client.post("/videos/zzzzzzzzzzz/retry")
    assert db.get_video("zzzzzzzzzzz")["attempts"] == 0
    assert worker.enqueued == [(["zzzzzzzzzzz"], "manual", True)]


def test_search_and_save(client, worker, monkeypatch):
    monkeypatch.setattr(web, "find_videos", lambda q, key, **kw: [
        Video("dQw4w9WgXcQ", "Song", "Rick", "2009-10-25T00:00:00Z")])
    body = client.get("/search?q=rick&days=0").get_data(as_text=True)
    assert "Song" in body and 'value="dQw4w9WgXcQ"' in body
    client.post("/search/save", data={"q": "rick", "video_id": ["dQw4w9WgXcQ"],
                                      "title_dQw4w9WgXcQ": "Song"})
    assert worker.enqueued == [(["dQw4w9WgXcQ"], "search: rick", True)]


def test_search_without_key(client, settings):
    settings.youtube_api_key = None
    body = client.get("/search?q=x").get_data(as_text=True)
    assert "YOUTUBE_API_KEY is not set" in body


def test_watch_crud(client, worker):
    form = {"name": "AI", "query": "ai news", "schedule_kind": "daily", "daily_time": "07:30",
            "max_results": "5", "lookback_days": "3", "order_by": "date", "enabled": "1"}
    assert client.post("/watches/new", data=form).status_code == 302
    db = client.application.config["DB"]
    (watch,) = db.list_watches()
    assert watch["daily_time"] == "07:30" and watch["lookback_days"] == 3
    assert worker.scheduled[-1]["name"] == "AI"

    # Duplicate names are rejected.
    assert client.post("/watches/new", data=form).status_code == 400

    client.post(f"/watches/{watch['id']}/edit", data={**form, "schedule_kind": "interval",
                                                      "every_value": "2", "every_unit": "hours"})
    watch = db.get_watch(watch["id"])
    assert watch["schedule_kind"] == "interval" and watch["every_value"] == 2

    client.post(f"/watches/{watch['id']}/toggle")
    assert db.get_watch(watch["id"])["enabled"] == 0
    client.post(f"/watches/{watch['id']}/run")
    assert worker.ran == [watch["id"]]
    assert "AI" in client.get("/watches").get_data(as_text=True)
    client.post(f"/watches/{watch['id']}/delete")
    assert db.list_watches() == []


@pytest.mark.parametrize("override,message", [
    ({"name": ""}, "Give the watch a name"),
    ({"schedule_kind": "interval", "every_value": "5", "every_unit": "minutes"}, "at most every"),
    ({"schedule_kind": "daily", "daily_time": "25:00"}, "Daily time"),
    ({"schedule_kind": "cron", "cron": "nope"}, "Invalid cron"),
    ({"max_results": "500"}, "between 1 and 50"),
])
def test_watch_form_validation(override, message):
    form = {"name": "n", "query": "q", "schedule_kind": "daily", "daily_time": "07:00", **override}
    _, errors = web.parse_watch_form(form)
    assert any(message in e for e in errors), errors


def test_basic_auth(settings, worker):
    settings.app_password = "s3cret"
    client = web.create_app(settings, start_worker=False, worker=worker).test_client()
    assert client.get("/").status_code == 401
    assert client.get("/healthz").status_code == 200
    good = base64.b64encode(b"admin:s3cret").decode()
    assert client.get("/", headers={"Authorization": f"Basic {good}"}).status_code == 200
    bad = base64.b64encode(b"admin:nope").decode()
    assert client.get("/", headers={"Authorization": f"Basic {bad}"}).status_code == 401


CHANNEL = Channel("UC" + "a" * 22, "Veritasium", "@veritasium")


def test_search_by_channel(client, worker, monkeypatch):
    seen = {}
    monkeypatch.setattr(web, "resolve_channel", lambda value, key: CHANNEL)

    def fake_find(q, key, **kw):
        seen.update(kw, q=q)
        return [Video("dQw4w9WgXcQ", "Song", "Veritasium", "2026-09-01T00:00:00Z",
                      channel_id=CHANNEL.channel_id)]

    monkeypatch.setattr(web, "find_videos", fake_find)
    body = client.get("/search?channel=@veritasium").get_data(as_text=True)
    assert seen["q"] == "" and seen["channel_id"] == CHANNEL.channel_id
    assert seen["order"] == "date"  # a channel on its own defaults to newest uploads
    assert "Latest uploads" in body and "Save as a schedule" in body
    assert f"channel={CHANNEL.channel_id}" in body

    client.post("/search/save", data={"q": "", "channel_title": "Veritasium",
                                      "video_id": ["dQw4w9WgXcQ"]})
    assert worker.enqueued[-1] == (["dQw4w9WgXcQ"], "search: Veritasium", True)


def test_search_results_link_to_channel(client, monkeypatch):
    monkeypatch.setattr(web, "find_videos", lambda q, key, **kw: [
        Video("dQw4w9WgXcQ", "Song", "Rick", "2009-10-25T00:00:00Z", channel_id=CHANNEL.channel_id)])
    body = client.get("/search?q=rick").get_data(as_text=True)
    assert f"/search?channel={CHANNEL.channel_id}" in body
    assert "Schedule channel" in body


def test_channel_schedule(client, worker, monkeypatch):
    lookups = []

    def fake_resolve(value, key):
        lookups.append(value)
        return CHANNEL

    monkeypatch.setattr(web, "resolve_channel", fake_resolve)
    form = {"name": "", "query": "", "channel": "https://youtube.com/@veritasium",
            "schedule_kind": "daily", "daily_time": "07:30", "max_results": "5",
            "lookback_days": "3", "order_by": "date", "enabled": "1"}
    assert client.post("/watches/new", data=form).status_code == 302
    db = client.application.config["DB"]
    (watch,) = db.list_watches()
    assert (watch["name"], watch["query"]) == ("Veritasium", "")
    assert (watch["channel_id"], watch["channel_title"]) == (CHANNEL.channel_id, "Veritasium")
    assert "all uploads" in client.get("/watches").get_data(as_text=True)

    # Editing without touching the channel doesn't look it up again.
    edit = client.get(f"/watches/{watch['id']}/edit").get_data(as_text=True)
    assert 'value="Veritasium"' in edit
    client.post(f"/watches/{watch['id']}/edit",
                data={**form, "name": "Veritasium", "channel": "Veritasium", "query": "physics"})
    watch = db.get_watch(watch["id"])
    assert watch["query"] == "physics" and watch["channel_id"] == CHANNEL.channel_id
    assert lookups == ["https://youtube.com/@veritasium"]

    # Clearing the channel needs keywords instead.
    client.post(f"/watches/{watch['id']}/edit",
                data={**form, "name": "Veritasium", "channel": "", "query": "physics"})
    assert db.get_watch(watch["id"])["channel_id"] is None


def test_channel_schedule_unknown_channel(client, monkeypatch):
    def fail(value, key):
        raise web.YouTubeError("Couldn't find a YouTube channel for 'nope'.")

    monkeypatch.setattr(web, "resolve_channel", fail)
    resp = client.post("/watches/new", data={"channel": "nope", "schedule_kind": "daily",
                                             "daily_time": "07:00"})
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "Couldn&#39;t find a YouTube channel" in body and 'value="nope"' in body


def test_watch_needs_keywords_or_channel():
    _, errors = web.parse_watch_form({"name": "n", "query": "", "schedule_kind": "daily",
                                      "daily_time": "07:00"})
    assert any("keywords, a channel" in e for e in errors)


def test_index_filters_by_channel(client):
    db = client.application.config["DB"]
    db.upsert_video(Video("dQw4w9WgXcQ", "Song", "Rick"), status="saved", note_path="a.md")
    db.upsert_video(Video("zzzzzzzzzzz", "Physics", "Veritasium"), status="saved", note_path="b.md")
    body = client.get("/?channel=Veritasium").get_data(as_text=True)
    assert "Physics" in body and "Song" not in body
    assert "Rick (1)" in body and "Veritasium (1)" in body
