"""The per-video pipeline: metadata -> transcript -> Obsidian note."""

from __future__ import annotations

import logging

from .config import Settings
from .db import Database
from . import writer
from .notes import render_note, write_note
from .transcripts import TranscriptError, fetch_transcript
from .youtube import Video, get_video

log = logging.getLogger(__name__)

# Transient failures are retried (on "Retry", or when a watch finds the video
# again) up to this many attempts.
MAX_ATTEMPTS = 5


def needs_processing(row) -> bool:
    """Whether a video (DB row or None) should be fetched."""
    if row is None:
        return True
    return row["status"] == "failed" and row["attempts"] < MAX_ATTEMPTS


def process_video(
    video: Video,
    settings: Settings,
    db: Database,
    *,
    source: str | None = None,
    watch_id: int | None = None,
    force: bool = False,
) -> str:
    """Fetch one transcript and save it to the output folder. Returns the final status.

    A note that already exists for the video (e.g. written by an older version, or
    by hand) is kept as is unless `force` is set. Never raises for per-video
    problems; they're recorded on the video row.
    """
    try:
        existing = writer.find_existing(settings.output_dir, video.video_id)
    except OSError as exc:
        db.upsert_video(video, status="failed", source=source, watch_id=watch_id,
                        error=f"Output folder unavailable: {exc}")
        log.error("Output folder %s unavailable: %s", settings.output_dir, exc)
        return "failed"
    if existing and not force:
        if not video.title:  # recover it from "<title> (<video_id>).md"
            video = Video(video.video_id, existing.name.removesuffix(f" ({video.video_id}).md"),
                          video.channel, video.published_at)
        db.upsert_video(video, status="saved", source=source, watch_id=watch_id,
                        error=None, note_path=existing.name)
        log.info("%s already has a note: %s", video.video_id, existing.name)
        return "saved"

    if not video.title:
        video = get_video(video.video_id, settings.youtube_api_key)
    db.upsert_video(video, status="processing", source=source, watch_id=watch_id,
                    count_attempt=True)

    log.info("Fetching transcript for %s (%s)", video.video_id, video.title or "untitled")
    try:
        transcript = fetch_transcript(video.video_id, settings.transcript_languages)
        row = db.get_video(video.video_id)
        content = render_note(video, transcript, source=row["source"] if row else "",
                              tags=settings.note_tags)
        path = write_note(settings.output_dir, video, content, replace=existing)
    except TranscriptError as exc:
        status = "failed" if not exc.permanent else "no_transcript"
        db.upsert_video(video, status=status, error=str(exc))
        log.warning("%s: %s", video.video_id, exc)
        return status
    except Exception as exc:  # unexpected (disk full, bad path, ...): keep it retryable
        db.upsert_video(video, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.exception("%s: failed to save transcript", video.video_id)
        return "failed"

    db.upsert_video(video, status="saved", error=None, note_path=path.name)
    log.info("Saved %s", path.name)
    return "saved"
