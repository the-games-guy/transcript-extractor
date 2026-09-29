"""Scheduled keyword watches."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import ConfigError, Settings
from .emailer import send_email
from .pipeline import ProcessResult, process_video
from .state import ProcessedStore
from .youtube import search_videos

log = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_duration(value: str | int) -> timedelta:
    """Parse '30m', '6h', '2d', '1w' (or an int number of minutes)."""
    if isinstance(value, int):
        return timedelta(minutes=value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ConfigError(f"Invalid duration {value!r}; use e.g. 30m, 6h, 2d, 1w")
    return timedelta(**{_UNITS[match.group(2).lower()]: int(match.group(1))})


@dataclass
class Watch:
    name: str
    query: str
    every: str | None = None
    cron: str | None = None
    timezone: str = "UTC"
    max_results: int = 5
    lookback: str = "7d"
    order: str = "date"
    channel_id: str | None = None
    min_duration: str | None = None
    email_to: list[str] = field(default_factory=list)
    summarize: bool = True
    email: bool = True

    def __post_init__(self):
        if not self.query:
            raise ConfigError(f"Watch {self.name!r} needs a query")
        if bool(self.every) == bool(self.cron):
            raise ConfigError(f"Watch {self.name!r} needs exactly one of 'every' or 'cron'")
        if isinstance(self.email_to, str):
            self.email_to = [e.strip() for e in self.email_to.split(",") if e.strip()]
        parse_duration(self.lookback)
        if self.every:
            parse_duration(self.every)

    def trigger(self):
        if self.every:
            return IntervalTrigger(seconds=parse_duration(self.every).total_seconds())
        return CronTrigger.from_crontab(self.cron, timezone=self.timezone)


def load_watches(path: Path) -> list[Watch]:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found; copy watches.example.yaml to get started")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("watches") or []
    known = set(Watch.__dataclass_fields__)
    watches = []
    for i, entry in enumerate(raw):
        entry = dict(entry or {})
        entry.setdefault("name", f"watch-{i + 1}")
        unknown = set(entry) - known
        if unknown:
            raise ConfigError(f"Watch {entry['name']!r} has unknown keys: {sorted(unknown)}")
        watches.append(Watch(**entry))
    names = [w.name for w in watches]
    if len(names) != len(set(names)):
        raise ConfigError("Watch names must be unique")
    return watches


def run_watch(
    watch: Watch,
    settings: Settings,
    store: ProcessedStore,
    *,
    send: bool = True,
) -> list[ProcessResult]:
    """Search once, process new videos, and email a digest. Returns the results."""
    since = datetime.now(timezone.utc) - parse_duration(watch.lookback)
    log.info("[%s] Searching %r since %s", watch.name, watch.query, since.date())
    videos = search_videos(
        watch.query,
        settings.require_youtube_key(),
        max_results=watch.max_results,
        published_after=since,
        order=watch.order,
        channel_id=watch.channel_id,
        video_duration=watch.min_duration,
    )
    new = [v for v in videos if v.video_id not in store]
    log.info("[%s] %d results, %d new", watch.name, len(videos), len(new))

    results = []
    for video in new:
        result = process_video(video, settings, with_summary=watch.summarize)
        results.append(result)
        # Leave transient failures unmarked so the next run retries them.
        if result.ok or not result.retryable:
            store.mark(
                video.video_id,
                watch=watch.name,
                title=video.title,
                path=result.markdown_path,
                error=result.error,
            )

    emailable = [r for r in results if r.ok]
    if send and watch.email and emailable:
        send_email(
            settings,
            [r.email_item() for r in emailable],
            recipients=watch.email_to or None,
            subject=f"[{watch.name}] {len(emailable)} new video summar"
            + ("y" if len(emailable) == 1 else "ies"),
        )
        log.info("[%s] Emailed %d summaries", watch.name, len(emailable))
    return results


def _safe_run(watch: Watch, settings: Settings, store: ProcessedStore, send: bool) -> None:
    try:
        run_watch(watch, settings, store, send=send)
    except Exception:
        log.exception("[%s] Run failed", watch.name)


def run_scheduler(watches: list[Watch], settings: Settings, *, send: bool = True) -> None:
    """Run all watches on their schedules until interrupted."""
    settings.require_youtube_key()
    store = ProcessedStore(settings.state_file)
    scheduler = BlockingScheduler(timezone="UTC")
    now = datetime.now(timezone.utc)
    for watch in watches:
        # Interval watches run immediately at startup; cron watches wait for their slot.
        # (Passing next_run_time=None would add the job paused, so only set it when needed.)
        extra = {"next_run_time": now} if watch.every else {}
        scheduler.add_job(
            _safe_run,
            trigger=watch.trigger(),
            args=[watch, settings, store, send],
            id=watch.name,
            name=watch.name,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
            **extra,
        )
    log.info("Scheduled %d watch(es): %s", len(watches), ", ".join(w.name for w in watches))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped")
