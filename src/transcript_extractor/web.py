"""The web interface."""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from flask import (
    Flask, Response, abort, flash, redirect, render_template, request, url_for,
)

from .config import ConfigError, Settings
from .db import PENDING_STATUSES, Database
from .notes import SUMMARY_PLACEHOLDER
from .scheduler import MIN_INTERVAL_MINUTES, UNITS, Worker, describe_schedule
from .youtube import Video, YouTubeError, parse_video_id, search_videos

log = logging.getLogger(__name__)

ORDERS = {"date": "Newest", "relevance": "Relevance", "viewCount": "Most viewed",
          "rating": "Top rated"}
DURATIONS = {"": "Any length", "short": "Under 4 min", "medium": "4-20 min",
             "long": "Over 20 min"}
STATUS_LABELS = {"queued": "Queued", "processing": "Fetching", "saved": "Saved",
                 "no_transcript": "No transcript", "failed": "Failed"}


def _secret_key(settings: Settings) -> str:
    if settings.secret_key:
        return settings.secret_key
    path = settings.data_dir / "secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
    return path.read_text().strip()


def parse_watch_form(form) -> tuple[dict, list[str]]:
    """Validate the watch form. Returns (data, errors)."""
    errors: list[str] = []

    def as_int(name, default, lo, hi, label):
        raw = (form.get(name) or "").strip()
        try:
            value = int(raw) if raw else default
        except ValueError:
            errors.append(f"{label} must be a whole number.")
            return default
        if not lo <= value <= hi:
            errors.append(f"{label} must be between {lo} and {hi}.")
        return value

    data = {
        "name": (form.get("name") or "").strip(),
        "query": (form.get("query") or "").strip(),
        "schedule_kind": form.get("schedule_kind") or "interval",
        "every_value": None, "every_unit": None, "daily_time": None, "cron": None,
        "max_results": as_int("max_results", 5, 1, 50, "Videos per check"),
        "lookback_days": as_int("lookback_days", 7, 1, 365, "Look-back window"),
        "order_by": form.get("order_by") if form.get("order_by") in ORDERS else "date",
        "channel_id": (form.get("channel_id") or "").strip() or None,
        "duration": form.get("duration") if form.get("duration") in DURATIONS else "",
        "enabled": 1 if form.get("enabled") else 0,
    }
    if not data["name"]:
        errors.append("Give the watch a name.")
    if not data["query"]:
        errors.append("Enter search keywords.")

    kind = data["schedule_kind"]
    if kind == "interval":
        data["every_unit"] = form.get("every_unit") if form.get("every_unit") in UNITS else "hours"
        data["every_value"] = as_int("every_value", 6, 1, 10_000, "Interval")
        if data["every_value"] * UNITS[data["every_unit"]] < MIN_INTERVAL_MINUTES:
            errors.append(f"Check at most every {MIN_INTERVAL_MINUTES} minutes.")
    elif kind == "daily":
        value = (form.get("daily_time") or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
            errors.append("Daily time must look like 07:30.")
        data["daily_time"] = value
    elif kind == "cron":
        value = (form.get("cron") or "").strip()
        try:
            CronTrigger.from_crontab(value)
        except ValueError as exc:
            errors.append(f"Invalid cron expression: {exc}")
        data["cron"] = value
    else:
        errors.append("Pick a schedule.")
    return data, errors


def create_app(settings: Settings, *, start_worker: bool = True,
               worker: Worker | None = None) -> Flask:
    app = Flask(__name__)
    app.secret_key = _secret_key(settings)
    db = Database(settings.db_path)
    worker = worker or Worker(settings, db)
    app.config.update(SETTINGS=settings, DB=db, WORKER=worker)
    if start_worker:
        worker.start()

    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    # --- auth ----------------------------------------------------------------

    @app.before_request
    def require_auth():
        if not settings.app_password or request.endpoint in ("healthz", "static"):
            return None
        auth = request.authorization
        if auth and auth.type == "basic" and hmac.compare_digest(
            (auth.username or "").encode(), settings.app_username.encode()
        ) and hmac.compare_digest((auth.password or "").encode(), settings.app_password.encode()):
            return None
        return Response("Login required", 401,
                        {"WWW-Authenticate": 'Basic realm="Transcript Extractor"'})

    # --- template helpers ----------------------------------------------------

    @app.template_filter("localtime")
    def localtime(value) -> str:
        if not value:
            return ""
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%d %b %Y, %H:%M")

    @app.template_filter("ago")
    def ago(value) -> str:
        if not value:
            return ""
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        future = secs < 0
        secs = abs(secs)
        for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
            if secs >= size:
                text = f"{secs // size}{unit}"
                break
        else:
            text = "moments" if not future else "<1m"
        return f"in {text}" if future else (f"{text} ago" if text != "moments" else "just now")

    def obsidian_link(note_path: str | None) -> str | None:
        if not note_path or not settings.obsidian_vault_name:
            return None
        file = note_path[:-3] if note_path.endswith(".md") else note_path
        if settings.vault_folder:
            file = f"{settings.vault_folder}/{file}"
        return (f"obsidian://open?vault={quote(settings.obsidian_vault_name)}"
                f"&file={quote(file)}")

    @app.context_processor
    def inject():
        return {
            "settings": settings, "status_labels": STATUS_LABELS,
            "obsidian_link": obsidian_link, "has_youtube_key": bool(settings.youtube_api_key),
            "ORDERS": ORDERS, "DURATIONS": DURATIONS, "describe_schedule": describe_schedule,
        }

    # --- pages ---------------------------------------------------------------

    @app.get("/")
    def index():
        status = request.args.get("status") or None
        if status not in STATUS_LABELS:
            status = None
        videos = db.recent_videos(limit=100, status=status)
        counts = db.status_counts()
        pending = sum(counts.get(s, 0) for s in PENDING_STATUSES)
        return render_template("index.html", videos=videos, counts=counts,
                               pending=pending, status=status)

    @app.post("/videos")
    def add_videos():
        raw = request.form.get("urls", "")
        entries = [e for e in re.split(r"[\s,]+", raw) if e]
        videos, bad = [], []
        for entry in entries:
            try:
                videos.append(Video(video_id=parse_video_id(entry)))
            except YouTubeError:
                bad.append(entry)
        if bad:
            flash(f"Not a YouTube video link: {', '.join(bad[:5])}", "error")
        if videos:
            queued, skipped = worker.enqueue(videos, source="manual", force=True)
            _flash_queue(queued, skipped)
        elif not bad:
            flash("Paste at least one YouTube link.", "error")
        return redirect(url_for("index"))

    @app.post("/videos/<video_id>/retry")
    def retry_video(video_id):
        row = db.get_video(video_id)
        if row is None:
            abort(404)
        if row["status"] in ("failed", "no_transcript"):
            db.reset_attempts(video_id)  # a manual retry gets a fresh set of attempts
            worker.enqueue([Video(video_id=video_id, title=row["title"], channel=row["channel"],
                                  published_at=row["published_at"])],
                           source=row["source"] or "manual", force=True)
            flash("Queued again.", "ok")
        return redirect(request.referrer or url_for("index"))

    @app.get("/search")
    def search():
        q = (request.args.get("q") or "").strip()
        form = {
            "q": q,
            "days": request.args.get("days", "30"),
            "order": request.args.get("order") if request.args.get("order") in ORDERS
            else "relevance",
            "duration": request.args.get("duration") if request.args.get("duration")
            in DURATIONS else "",
            "max": request.args.get("max", "10"),
        }
        results, error, existing = [], None, {}
        if q:
            try:
                days = int(form["days"] or 0)
                results = search_videos(
                    q, settings.require_youtube_key(),
                    max_results=int(form["max"] or 10),
                    published_after=(datetime.now(timezone.utc) - timedelta(days=days))
                    if days else None,
                    order=form["order"], video_duration=form["duration"] or None,
                )
                existing = db.get_videos([v.video_id for v in results])
            except (ConfigError, YouTubeError, ValueError) as exc:
                error = str(exc)
            except Exception as exc:
                log.exception("Search failed")
                error = f"Search failed: {type(exc).__name__}: {exc}"
        return render_template("search.html", form=form, results=results, error=error,
                               existing=existing)

    @app.post("/search/save")
    def search_save():
        ids = request.form.getlist("video_id")
        videos = [
            Video(video_id=parse_video_id(vid),
                  title=request.form.get(f"title_{vid}", ""),
                  channel=request.form.get(f"channel_{vid}", ""),
                  published_at=request.form.get(f"published_{vid}", ""))
            for vid in ids
        ]
        if not videos:
            flash("Tick at least one video to save.", "error")
            return redirect(request.referrer or url_for("search"))
        query = request.form.get("q", "")
        queued, skipped = worker.enqueue(videos, source=f"search: {query}" if query else "search",
                                         force=True)
        _flash_queue(queued, skipped)
        return redirect(url_for("index"))

    @app.get("/watches")
    def watches():
        rows = db.list_watches()
        return render_template("watches.html", watches=rows, last_runs=db.last_runs(),
                               next_run=worker.next_run, is_running=worker.is_running)

    @app.route("/watches/new", methods=["GET", "POST"])
    def new_watch():
        if request.method == "POST":
            data, errors = parse_watch_form(request.form)
            if not errors:
                try:
                    watch_id = db.create_watch(data)
                except Exception as exc:
                    if "UNIQUE" not in str(exc):
                        raise
                    errors.append("A watch with that name already exists.")
                else:
                    worker.schedule(db.get_watch(watch_id))
                    flash(f"Watch “{data['name']}” created.", "ok")
                    return redirect(url_for("watches"))
            for e in errors:
                flash(e, "error")
            return render_template("watch_form.html", watch=data, is_new=True), 400
        defaults = {
            "name": request.args.get("q", ""), "query": request.args.get("q", ""),
            "schedule_kind": "interval", "every_value": 6, "every_unit": "hours",
            "daily_time": "07:00", "cron": "", "max_results": 5, "lookback_days": 7,
            "order_by": "date", "channel_id": "", "duration": request.args.get("duration", ""),
            "enabled": 1,
        }
        return render_template("watch_form.html", watch=defaults, is_new=True)

    @app.route("/watches/<int:watch_id>/edit", methods=["GET", "POST"])
    def edit_watch(watch_id):
        watch = db.get_watch(watch_id) or abort(404)
        if request.method == "POST":
            data, errors = parse_watch_form(request.form)
            if not errors:
                try:
                    db.update_watch(watch_id, data)
                except Exception as exc:
                    if "UNIQUE" not in str(exc):
                        raise
                    errors.append("A watch with that name already exists.")
                else:
                    worker.schedule(db.get_watch(watch_id))
                    flash("Watch updated.", "ok")
                    return redirect(url_for("watches"))
            for e in errors:
                flash(e, "error")
            return render_template("watch_form.html", watch={**data, "id": watch_id},
                                   is_new=False), 400
        return render_template("watch_form.html", watch=dict(watch), is_new=False)

    @app.post("/watches/<int:watch_id>/toggle")
    def toggle_watch(watch_id):
        watch = db.get_watch(watch_id) or abort(404)
        db.update_watch(watch_id, {"enabled": 0 if watch["enabled"] else 1})
        worker.schedule(db.get_watch(watch_id))
        return redirect(url_for("watches"))

    @app.post("/watches/<int:watch_id>/run")
    def run_watch(watch_id):
        watch = db.get_watch(watch_id) or abort(404)
        if not settings.youtube_api_key:
            flash("Set YOUTUBE_API_KEY to run watches.", "error")
        else:
            worker.run_watch_now(watch_id)
            flash(f"Checking “{watch['name']}” now - new transcripts will appear on "
                  "the Transcripts page.", "ok")
        return redirect(url_for("watches"))

    @app.post("/watches/<int:watch_id>/delete")
    def delete_watch(watch_id):
        watch = db.get_watch(watch_id) or abort(404)
        worker.unschedule(watch_id)
        db.delete_watch(watch_id)
        flash(f"Deleted “{watch['name']}”. Saved transcripts are kept.", "ok")
        return redirect(url_for("watches"))

    @app.get("/routine")
    def routine():
        return render_template("routine.html", placeholder=SUMMARY_PLACEHOLDER)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def _flash_queue(queued: int, skipped: int) -> None:
    if queued:
        flash(f"Fetching {queued} transcript{'s' if queued != 1 else ''}…", "ok")
    if skipped:
        flash(f"{skipped} already in your vault (or has no transcript) - skipped.", "info")
