import base64

import pytest

from transcript_extractor import web
from transcript_extractor.youtube import Video


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
                    note_path="YouTube/2009-10-25 Song.md", source="manual")
    db.upsert_video(Video("zzzzzzzzzzz", "Broken"), status="failed", error="IpBlocked: nope")
    body = client.get("/").get_data(as_text=True)
    assert "obsidian://open?vault=My%20Vault&amp;file=YouTube/2009-10-25%20Song" in body
    assert "IpBlocked: nope" in body and "Retry" in body
    assert "Broken" not in client.get("/?status=saved").get_data(as_text=True)


def test_retry_resets_attempts(client, worker):
    db = client.application.config["DB"]
    db.upsert_video(Video("zzzzzzzzzzz", "Broken"), status="failed", count_attempt=True)
    client.post("/videos/zzzzzzzzzzz/retry")
    assert db.get_video("zzzzzzzzzzz")["attempts"] == 0
    assert worker.enqueued == [(["zzzzzzzzzzz"], "manual", True)]


def test_search_and_save(client, worker, monkeypatch):
    monkeypatch.setattr(web, "search_videos", lambda q, key, **kw: [
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
