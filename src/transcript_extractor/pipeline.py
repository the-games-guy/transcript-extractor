"""The per-video pipeline: metadata -> transcript -> Obsidian note."""

from __future__ import annotations

import logging

from .config import Settings
from .db import Database
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
) -> str:
    """Fetch one transcript and save it to the vault. Returns the final status.

    Never raises for per-video problems; they're recorded on the video row.
    """
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
        path = write_note(settings.notes_dir, video, content)
    except TranscriptError as exc:
        status = "failed" if not exc.permanent else "no_transcript"
        db.upsert_video(video, status=status, error=str(exc))
        log.warning("%s: %s", video.video_id, exc)
        return status
    except Exception as exc:  # unexpected (disk full, bad path, ...): keep it retryable
        db.upsert_video(video, status="failed", error=f"{type(exc).__name__}: {exc}")
        log.exception("%s: failed to save transcript", video.video_id)
        return "failed"

    rel = path.relative_to(settings.vault_dir).as_posix()
    db.upsert_video(video, status="saved", error=None, note_path=rel)
    log.info("Saved %s", rel)
    return "saved"
