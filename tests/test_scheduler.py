from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from transcript_extractor import scheduler
from transcript_extractor.scheduler import Worker, build_trigger, describe_schedule
from transcript_extractor.youtube import Video

BASE = {"schedule_kind": "interval", "every_value": 6, "every_unit": "hours",
        "daily_time": None, "cron": None}


def test_triggers_and_descriptions():
    assert isinstance(build_trigger(BASE, "UTC"), IntervalTrigger)
    assert describe_schedule(BASE) == "Every 6 hours"
    assert describe_schedule({**BASE, "every_value": 1, "every_unit": "days"}) == "Every 1 day"
    daily = {**BASE, "schedule_kind": "daily", "daily_time": "07:30"}
    assert isinstance(build_trigger(daily, "Europe/London"), CronTrigger)
    assert describe_schedule(daily) == "Daily at 07:30"
    cron = {**BASE, "schedule_kind": "cron", "cron": "0 7 * * 1-5"}
    assert isinstance(build_trigger(cron, "UTC"), CronTrigger)


def _watch(db, **kw):
    data = {"name": "w", "query": "q", "schedule_kind": "interval", "every_value": 6,
            "every_unit": "hours", "max_results": 5, "lookback_days": 7, "order_by": "date",
            "enabled": 1, **kw}
    return db.create_watch(data)


def test_run_watch_processes_only_new(monkeypatch, settings, db):
    videos = [Video("aaaaaaaaaaa", "A"), Video("bbbbbbbbbbb", "B"), Video("ccccccccccc", "C")]
    searched = {}

    def fake_search(query, key, **kw):
        searched.update(kw, query=query)
        return videos

    processed = []

    def fake_process(video, settings, db, *, source, watch_id):
        processed.append(video.video_id)
        status = "no_transcript" if video.video_id == "ccccccccccc" else "saved"
        db.upsert_video(video, status=status, source=source, watch_id=watch_id)
        return status

    monkeypatch.setattr(scheduler, "search_videos", fake_search)
    monkeypatch.setattr(scheduler, "process_video", fake_process)
    db.upsert_video(videos[0], status="saved")  # already have A

    watch_id = _watch(db, lookback_days=2)
    worker = Worker(settings, db)
    counts = worker.run_watch(watch_id)

    assert processed == ["bbbbbbbbbbb", "ccccccccccc"]
    assert counts == {"found": 3, "new": 2, "saved": 1, "failed": 1}
    assert searched["query"] == "q" and searched["max_results"] == 5
    assert datetime.now(timezone.utc) - searched["published_after"] < timedelta(days=2, minutes=1)
    last = db.last_runs()[watch_id]
    assert last["finished_at"] and last["saved"] == 1 and last["error"] is None
    assert db.get_video("bbbbbbbbbbb")["source"] == "watch: w"

    # A second run finds nothing new.
    processed.clear()
    assert worker.run_watch(watch_id)["new"] == 0 and processed == []
    worker.shutdown()


def test_run_watch_records_errors(settings, db):
    settings.youtube_api_key = None
    watch_id = _watch(db)
    worker = Worker(settings, db)
    worker.run_watch(watch_id)
    assert "YOUTUBE_API_KEY" in db.last_runs()[watch_id]["error"]
    worker.shutdown()


def test_schedule_resumes_interval_cadence(settings, db):
    watch_id = _watch(db)
    run_id = db.start_run(watch_id)
    db.finish_run(run_id, found=0)
    worker = Worker(settings, db)
    worker.scheduler.start(paused=True)
    try:
        worker.schedule(db.get_watch(watch_id))
        nxt = worker.next_run(watch_id)
        # Last run was just now, so the next one is ~6h away rather than immediately.
        assert nxt - datetime.now(timezone.utc) > timedelta(hours=5, minutes=59)
        db.update_watch(watch_id, {"enabled": 0})
        worker.schedule(db.get_watch(watch_id))
        assert worker.next_run(watch_id) is None
    finally:
        worker.shutdown()


def test_enqueue_skips_saved_and_retries_on_force(monkeypatch, settings, db):
    submitted = []
    worker = Worker(settings, db)
    monkeypatch.setattr(worker, "_submit", lambda v, s, w: submitted.append(v.video_id))
    db.upsert_video(Video("aaaaaaaaaaa"), status="saved")
    db.upsert_video(Video("bbbbbbbbbbb"), status="no_transcript")
    vids = [Video("aaaaaaaaaaa"), Video("bbbbbbbbbbb"), Video("ccccccccccc"),
            Video("ccccccccccc")]

    assert worker.enqueue(vids, source="search") == (1, 2)
    assert submitted == ["ccccccccccc"]
    assert db.get_video("ccccccccccc")["status"] == "queued"

    submitted.clear()
    db.upsert_video(Video("ccccccccccc"), status="saved")
    assert worker.enqueue(vids, source="manual", force=True) == (1, 2)
    assert submitted == ["bbbbbbbbbbb"]
    worker.shutdown()
