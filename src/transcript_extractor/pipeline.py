"""The per-video pipeline: metadata -> transcript -> summary -> Markdown file."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .emailer import EmailItem
from .markdown import render_markdown, save_markdown
from .summarizer import summarize
from .transcripts import Transcript, TranscriptError, fetch_transcript
from .youtube import Video, get_video

log = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    video: Video
    transcript: Transcript | None = None
    markdown_path: Path | None = None
    summary: str | None = None
    error: str | None = None
    # True when a failure may succeed on a later attempt (network trouble, rate limits).
    retryable: bool = False

    @property
    def ok(self) -> bool:
        return self.markdown_path is not None

    def email_item(self) -> EmailItem:
        if self.summary:
            body = self.summary
        elif self.error:
            body = f"_No summary: {self.error}_"
        else:
            body = "_Summary disabled._"
        return EmailItem(
            title=self.video.title or self.video.video_id,
            url=self.video.url,
            channel=self.video.channel,
            summary=body,
            markdown_path=self.markdown_path,
        )


def process_video(
    video: Video | str,
    settings: Settings,
    *,
    with_summary: bool = True,
) -> ProcessResult:
    """Fetch the transcript for one video, summarize it, and save it as Markdown.

    Never raises for per-video problems (no captions, API errors); they are
    reported on the result so batch runs can continue.
    """
    if isinstance(video, str):
        video = get_video(video, settings.youtube_api_key)
    elif not video.title:
        video = get_video(video.video_id, settings.youtube_api_key)
    result = ProcessResult(video=video)

    log.info("Fetching transcript for %s (%s)", video.video_id, video.title or "untitled")
    try:
        result.transcript = fetch_transcript(video.video_id, settings.transcript_languages)
    except TranscriptError as exc:
        result.retryable = not exc.permanent
        result.error = (
            f"Transcript unavailable - {exc}" if exc.permanent else f"Transcript fetch failed - {exc}"
        )
        log.warning("%s: %s", video.video_id, result.error)
        return result

    if with_summary:
        log.info("Summarizing %s with %s", video.video_id, settings.anthropic_model)
        try:
            result.summary = summarize(video, result.transcript, model=settings.anthropic_model)
        except Exception as exc:  # keep the transcript even if summarizing fails
            result.error = f"Summary failed - {type(exc).__name__}: {exc}"
            log.warning("%s: %s", video.video_id, result.error)

    content = render_markdown(video, result.transcript, result.summary)
    result.markdown_path = save_markdown(settings.transcripts_dir, video, content)
    log.info("Saved %s", result.markdown_path)
    return result
