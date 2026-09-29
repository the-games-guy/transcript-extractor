from datetime import timedelta

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from transcript_extractor import scheduler
from transcript_extractor.config import ConfigError
from transcript_extractor.pipeline import ProcessResult
from transcript_extractor.scheduler import Watch, load_watches, parse_duration, run_watch
from transcript_extractor.state import ProcessedStore
from transcript_extractor.youtube import Video


def test_parse_duration():
    assert parse_duration("30m") == timedelta(minutes=30)
    assert parse_duration("6H") == timedelta(hours=6)
    assert parse_duration("1w") == timedelta(weeks=1)
    assert parse_duration(15) == timedelta(minutes=15)
    with pytest.raises(ConfigError):
        parse_duration("soon")


def test_watch_validation_and_triggers():
    assert isinstance(Watch(name="a", query="q", every="1h").trigger(), IntervalTrigger)
    assert isinstance(Watch(name="b", query="q", cron="0 7 * * 1").trigger(), CronTrigger)
    with pytest.raises(ConfigError):
        Watch(name="c", query="q")
    with pytest.raises(ConfigError):
        Watch(name="d", query="q", every="1h", cron="* * * * *")


def test_example_config_loads():
    watches = load_watches("watches.example.yaml")
    assert [w.name for w in watches] == ["ai-news", "python-talks"]


def test_load_watches_rejects_unknown_keys(tmp_path):
    path = tmp_path / "w.yaml"
    path.write_text("watches:\n  - name: x\n    query: q\n    every: 1h\n    typo: 1\n")
    with pytest.raises(ConfigError, match="typo"):
        load_watches(path)


def test_run_watch_processes_only_new_and_emails_digest(monkeypatch, settings, tmp_path):
    videos = [Video("aaaaaaaaaaa", "A"), Video("bbbbbbbbbbb", "B"), Video("ccccccccccc", "C")]
    monkeypatch.setattr(scheduler, "search_videos", lambda *a, **k: videos)

    def fake_process(video, settings, with_summary):
        if video.video_id == "bbbbbbbbbbb":
            return ProcessResult(video, error="Transcript unavailable - disabled")
        if video.video_id == "ccccccccccc":
            return ProcessResult(video, error="YouTube hiccup", retryable=True)
        path = tmp_path / f"{video.video_id}.md"
        path.write_text("x")
        return ProcessResult(video, markdown_path=path, summary="sum")

    sent = []
    monkeypatch.setattr(scheduler, "process_video", fake_process)
    monkeypatch.setattr(scheduler, "send_email", lambda s, items, **kw: sent.append((items, kw)))

    store = ProcessedStore(settings.state_file)
    store.mark("aaaaaaaaaaa")  # already seen
    videos.insert(0, Video("zzzzzzzzzzz", "Z"))

    results = run_watch(Watch(name="w", query="q", every="1h"), settings, store)
    assert [r.video.video_id for r in results] == ["zzzzzzzzzzz", "bbbbbbbbbbb", "ccccccccccc"]
    assert "zzzzzzzzzzz" in store and "bbbbbbbbbbb" in store and "ccccccccccc" not in store
    assert len(sent) == 1 and [i.title for i in sent[0][0]] == ["Z"]
    assert sent[0][1]["subject"] == "[w] 1 new video summary"

    # State persists across restarts.
    assert "zzzzzzzzzzz" in ProcessedStore(settings.state_file)
