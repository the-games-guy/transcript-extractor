"""SQLite storage for watches, saved videos, and watch run history."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    query         TEXT NOT NULL,
    schedule_kind TEXT NOT NULL DEFAULT 'interval',  -- interval | daily | cron
    every_value   INTEGER,
    every_unit    TEXT,                               -- minutes | hours | days
    daily_time    TEXT,                               -- HH:MM
    cron          TEXT,
    max_results   INTEGER NOT NULL DEFAULT 5,
    lookback_days INTEGER NOT NULL DEFAULT 7,
    order_by      TEXT NOT NULL DEFAULT 'date',
    channel_id    TEXT,
    channel_title TEXT,
    duration      TEXT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    video_id     TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,          -- queued | processing | saved | no_transcript | failed
    error        TEXT,
    note_path    TEXT,                   -- relative to the vault
    source       TEXT NOT NULL DEFAULT '',
    watch_id     INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS videos_updated ON videos(updated_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id    INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    found       INTEGER NOT NULL DEFAULT 0,
    new         INTEGER NOT NULL DEFAULT 0,
    saved       INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS runs_watch ON runs(watch_id, started_at DESC);
"""

WATCH_FIELDS = [
    "name", "query", "schedule_kind", "every_value", "every_unit", "daily_time", "cron",
    "max_results", "lookback_days", "order_by", "channel_id", "channel_title", "duration", "enabled",
]

# Columns added after the first release: (table, column, type).
MIGRATIONS = [("watches", "channel_title", "TEXT")]

PENDING_STATUSES = ("queued", "processing")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for table, column, kind in MIGRATIONS:
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    @contextmanager
    def connect(self):
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # --- watches -------------------------------------------------------------

    def list_watches(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM watches ORDER BY name COLLATE NOCASE").fetchall()

    def get_watch(self, watch_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()

    def create_watch(self, data: dict) -> int:
        cols = [f for f in WATCH_FIELDS if f in data]
        with self.connect() as conn:
            cur = conn.execute(
                f"INSERT INTO watches ({', '.join(cols)}, created_at) "
                f"VALUES ({', '.join('?' for _ in cols)}, ?)",
                [data[c] for c in cols] + [now_iso()],
            )
            return cur.lastrowid

    def update_watch(self, watch_id: int, data: dict) -> None:
        cols = [f for f in WATCH_FIELDS if f in data]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE watches SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                [data[c] for c in cols] + [watch_id],
            )

    def delete_watch(self, watch_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM runs WHERE watch_id = ?", (watch_id,))
            conn.execute("UPDATE videos SET watch_id = NULL WHERE watch_id = ?", (watch_id,))
            conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    # --- runs ----------------------------------------------------------------

    def start_run(self, watch_id: int) -> int:
        with self.connect() as conn:
            return conn.execute(
                "INSERT INTO runs (watch_id, started_at) VALUES (?, ?)", (watch_id, now_iso())
            ).lastrowid

    def finish_run(self, run_id: int, **counts) -> None:
        cols = [c for c in ("found", "new", "saved", "failed", "error") if c in counts]
        with self.connect() as conn:
            conn.execute(
                f"UPDATE runs SET finished_at = ?, {', '.join(f'{c} = ?' for c in cols)} "
                "WHERE id = ?",
                [now_iso()] + [counts[c] for c in cols] + [run_id],
            )

    def last_runs(self) -> dict[int, sqlite3.Row]:
        """Most recent run per watch."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.* FROM runs r JOIN (SELECT watch_id, MAX(id) AS id FROM runs "
                "GROUP BY watch_id) m ON r.id = m.id"
            ).fetchall()
        return {r["watch_id"]: r for r in rows}

    # --- videos --------------------------------------------------------------

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()

    def get_videos(self, video_ids: list[str]) -> dict[str, sqlite3.Row]:
        if not video_ids:
            return {}
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM videos WHERE video_id IN ({', '.join('?' for _ in video_ids)})",
                video_ids,
            ).fetchall()
        return {r["video_id"]: r for r in rows}

    @staticmethod
    def _video_filters(status: str | None = None, channel: str | None = None,
                       saved_from: str | None = None,
                       saved_before: str | None = None) -> tuple[str, list]:
        """WHERE clause for the videos list. Dates are UTC ISO bounds on created_at."""
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if channel:
            where.append("channel = ?")
            args.append(channel)
        if saved_from:
            where.append("created_at >= ?")
            args.append(saved_from)
        if saved_before:
            where.append("created_at < ?")
            args.append(saved_before)
        return (" WHERE " + " AND ".join(where) if where else ""), args

    def recent_videos(self, limit: int = 50, offset: int = 0, **filters) -> list[sqlite3.Row]:
        where, args = self._video_filters(**filters)
        sql = f"SELECT * FROM videos{where} ORDER BY updated_at DESC, video_id LIMIT ? OFFSET ?"
        with self.connect() as conn:
            return conn.execute(sql, args + [limit, offset]).fetchall()

    def count_videos(self, **filters) -> int:
        where, args = self._video_filters(**filters)
        with self.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM videos{where}", args).fetchone()[0]

    def status_counts(self, **filters) -> dict[str, int]:
        where, args = self._video_filters(**filters)
        with self.connect() as conn:
            rows = conn.execute(f"SELECT status, COUNT(*) AS n FROM videos{where} "
                                "GROUP BY status", args)
            return {r["status"]: r["n"] for r in rows}

    def prune_failed(self, older_than_days: int) -> int:
        """Delete failed / no-transcript rows untouched for `older_than_days`.

        Only the database rows go; no notes exist for these videos.
        """
        if older_than_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)) \
            .isoformat(timespec="seconds")
        with self.connect() as conn:
            return conn.execute(
                "DELETE FROM videos WHERE status IN ('failed', 'no_transcript') "
                "AND updated_at < ?", (cutoff,)
            ).rowcount

    def channels(self) -> list[sqlite3.Row]:
        """Channels with saved transcripts, most notes first."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT channel, COUNT(*) AS n FROM videos WHERE channel != '' AND status = 'saved' "
                "GROUP BY channel ORDER BY n DESC, channel COLLATE NOCASE"
            ).fetchall()

    def pending_video_ids(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT video_id FROM videos WHERE status IN (?, ?) ORDER BY created_at",
                PENDING_STATUSES,
            )
            return [r["video_id"] for r in rows]

    def reset_attempts(self, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE videos SET attempts = 0 WHERE video_id = ?", (video_id,))

    def upsert_video(self, video, *, status: str, source: str | None = None,
                     watch_id: int | None = None, error: str | None = None,
                     note_path: str | None = None, count_attempt: bool = False) -> None:
        ts = now_iso()
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT source, watch_id FROM videos WHERE video_id = ?", (video.video_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO videos (video_id, title, channel, published_at, status, error, "
                    "note_path, source, watch_id, attempts, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (video.video_id, video.title, video.channel, video.published_at, status,
                     error, note_path, source or "", watch_id, int(count_attempt), ts, ts),
                )
            else:
                conn.execute(
                    "UPDATE videos SET title = COALESCE(NULLIF(?, ''), title), "
                    "channel = COALESCE(NULLIF(?, ''), channel), "
                    "published_at = COALESCE(NULLIF(?, ''), published_at), "
                    "status = ?, error = ?, note_path = COALESCE(?, note_path), "
                    "source = COALESCE(?, source), watch_id = COALESCE(?, watch_id), "
                    "attempts = attempts + ?, updated_at = ? WHERE video_id = ?",
                    (video.title, video.channel, video.published_at, status, error, note_path,
                     source, watch_id, int(count_attempt), ts, video.video_id),
                )
