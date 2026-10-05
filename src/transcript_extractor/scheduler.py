"""Background work: the transcript queue and scheduled watches."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import ConfigError, Settings
from .db import Database
from .pipeline import needs_processing, process_video
from .youtube import Video, find_videos

log = logging.getLogger(__name__)

UNITS = {"minutes": 1, "hours": 60, "days": 1440}
MIN_INTERVAL_MINUTES = 15


def interval_minutes(watch) -> int:
    return int(watch["every_value"]) * UNITS[watch["every_unit"]]


def build_trigger(watch, tz: str):
    kind = watch["schedule_kind"]
    if kind == "interval":
        return IntervalTrigger(minutes=interval_minutes(watch), timezone=tz)
    if kind == "daily":
        hour, minute = (int(p) for p in watch["daily_time"].split(":"))
        return CronTrigger(hour=hour, minute=minute, timezone=tz)
    if kind == "cron":
        return CronTrigger.from_crontab(watch["cron"], timezone=tz)
    raise ConfigError(f"Unknown schedule kind {kind!r}")


def describe_schedule(watch) -> str:
    kind = watch["schedule_kind"]
    if kind == "interval":
        value, unit = watch["every_value"], watch["every_unit"]
        return f"Every {value} {unit[:-1] if value == 1 else unit}"
    if kind == "daily":
        return f"Daily at {watch['daily_time']}"
    return f"Cron: {watch['cron']}"


class Worker:
    """Owns the transcript queue and the watch scheduler."""

    def __init__(self, settings: Settings, db: Database, *, max_workers: int = 2):
        self.settings = settings
        self.db = db
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="fetch")
        self.scheduler = BackgroundScheduler(timezone=settings.timezone)
        self._watch_locks: dict[int, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        # Anything left mid-flight by a restart goes back on the queue.
        for video_id in self.db.pending_video_ids():
            row = self.db.get_video(video_id)
            self._submit(_video_from_row(row), row["source"], row["watch_id"])
        for watch in self.db.list_watches():
            self.schedule(watch)
        self.prune()
        self.scheduler.add_job(self.prune, IntervalTrigger(hours=24, timezone=self.settings.timezone),
                               id="prune-failed", replace_existing=True)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self.executor.shutdown(wait=False, cancel_futures=True)

    def prune(self) -> None:
        try:
            removed = self.db.prune_failed(self.settings.failed_retention_days)
        except Exception:  # noqa: BLE001 - never take the scheduler down
            log.exception("Pruning failed entries")
            return
        if removed:
            log.info("Removed %d failed entries older than %d days", removed,
                     self.settings.failed_retention_days)

    # --- queue ---------------------------------------------------------------

    def enqueue(self, videos: list[Video], *, source: str,
                watch_id: int | None = None, force: bool = False) -> tuple[int, int]:
        """Queue videos that still need a transcript. Returns (queued, skipped).

        `force` also retries videos that failed or had no transcript last time
        (for explicit user requests); saved videos are never fetched twice.
        """
        existing = self.db.get_videos([v.video_id for v in videos])
        queued = skipped = 0
        for video in dict((v.video_id, v) for v in videos).values():
            row = existing.get(video.video_id)
            retry = force and row is not None and row["status"] in ("failed", "no_transcript")
            if not (needs_processing(row) or retry):
                skipped += 1
                continue
            self.db.upsert_video(video, status="queued", source=source, watch_id=watch_id)
            self._submit(video, source, watch_id)
            queued += 1
        return queued, skipped

    def _submit(self, video: Video, source: str, watch_id: int | None) -> None:
        self.executor.submit(self._process_safely, video, source, watch_id)

    def _process_safely(self, video: Video, source: str, watch_id: int | None) -> str | None:
        try:
            return process_video(video, self.settings, self.db, source=source, watch_id=watch_id)
        except Exception:
            log.exception("Unexpected error processing %s", video.video_id)
            return None

    # --- watches -------------------------------------------------------------

    def schedule(self, watch) -> None:
        """Add, replace, or remove the job for a watch to match its DB row."""
        job_id = f"watch-{watch['id']}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
        if not watch["enabled"]:
            return
        extra = {}
        if watch["schedule_kind"] == "interval":
            # Resume the cadence across restarts instead of running everything at boot.
            last = self.db.last_runs().get(watch["id"])
            due = datetime.now(timezone.utc)
            if last:
                started = datetime.fromisoformat(last["started_at"])
                due = max(due, started + timedelta(minutes=interval_minutes(watch)))
            extra["next_run_time"] = due
        self.scheduler.add_job(
            self.run_watch, trigger=build_trigger(watch, self.settings.timezone),
            args=[watch["id"]], id=job_id, name=watch["name"],
            max_instances=1, coalesce=True, misfire_grace_time=3600, **extra,
        )

    def unschedule(self, watch_id: int) -> None:
        job_id = f"watch-{watch_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def next_run(self, watch_id: int) -> datetime | None:
        job = self.scheduler.get_job(f"watch-{watch_id}")
        return job.next_run_time if job else None

    def run_watch_now(self, watch_id: int) -> None:
        self.executor.submit(self.run_watch, watch_id)

    def is_running(self, watch_id: int) -> bool:
        return self._lock_for(watch_id).locked()

    def _lock_for(self, watch_id: int) -> threading.Lock:
        with self._locks_guard:
            return self._watch_locks.setdefault(watch_id, threading.Lock())

    def run_watch(self, watch_id: int) -> dict | None:
        """Search once and fetch transcripts for new videos. Returns run counts."""
        lock = self._lock_for(watch_id)
        if not lock.acquire(blocking=False):
            log.info("Watch %s is already running; skipping", watch_id)
            return None
        try:
            watch = self.db.get_watch(watch_id)
            if watch is None:
                return None
            run_id = self.db.start_run(watch_id)
            counts = {"found": 0, "new": 0, "saved": 0, "failed": 0}
            try:
                since = datetime.now(timezone.utc) - timedelta(days=watch["lookback_days"])
                videos = find_videos(
                    watch["query"] or "", self.settings.require_youtube_key(),
                    max_results=watch["max_results"], published_after=since,
                    order=watch["order_by"], channel_id=watch["channel_id"] or None,
                    video_duration=watch["duration"] or None,
                )
                existing = self.db.get_videos([v.video_id for v in videos])
                new = [v for v in videos if needs_processing(existing.get(v.video_id))]
                counts.update(found=len(videos), new=len(new))
                log.info("[%s] %d results, %d new", watch["name"], len(videos), len(new))
                for video in new:
                    status = process_video(video, self.settings, self.db,
                                           source=f"watch: {watch['name']}", watch_id=watch_id)
                    counts["saved" if status == "saved" else "failed"] += 1
                self.db.finish_run(run_id, **counts)
            except Exception as exc:
                log.exception("[%s] run failed", watch["name"])
                self.db.finish_run(run_id, **counts, error=f"{type(exc).__name__}: {exc}")
            return counts
        finally:
            lock.release()


def _video_from_row(row) -> Video:
    return Video(video_id=row["video_id"], title=row["title"], channel=row["channel"],
                 published_at=row["published_at"])
