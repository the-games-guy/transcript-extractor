"""Rendering transcripts (and summaries) to Markdown files."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from .transcripts import Transcript
from .youtube import Video

# Group transcript segments into paragraphs of roughly this many seconds.
PARAGRAPH_SECONDS = 60


def slugify(value: str, max_length: int = 80) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    value = re.sub(r"[-\s_]+", "-", value)
    return value[:max_length].strip("-")


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_markdown(video: Video, transcript: Transcript, summary: str | None = None) -> str:
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = video.title or video.video_id
    lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"video_id: {video.video_id}",
        f"url: {video.url}",
        f"channel: {_yaml_str(video.channel)}",
        f"published_at: {_yaml_str(video.published_at)}",
        f"language: {transcript.language_code}",
        f"auto_generated: {str(transcript.is_generated).lower()}",
        f"fetched_at: {fetched_at}",
        "---",
        "",
        f"# {title}",
        "",
    ]
    meta = [f"**Video:** [{video.url}]({video.url})"]
    if video.channel:
        meta.append(f"**Channel:** {video.channel}")
    if video.published_at:
        meta.append(f"**Published:** {video.published_at[:10]}")
    meta.append(
        f"**Transcript:** {transcript.language}"
        + (" (auto-generated)" if transcript.is_generated else "")
    )
    lines += ["  \n".join(meta), ""]

    if summary:
        lines += ["## Summary", "", summary.strip(), ""]

    lines += ["## Transcript", ""]
    paragraph: list[str] = []
    paragraph_start = None
    for seg in transcript.segments:
        if paragraph_start is None:
            paragraph_start = seg.start
        paragraph.append(seg.text)
        if seg.start + seg.duration - paragraph_start >= PARAGRAPH_SECONDS:
            lines += [_paragraph(video, paragraph_start, paragraph), ""]
            paragraph, paragraph_start = [], None
    if paragraph:
        lines += [_paragraph(video, paragraph_start or 0, paragraph), ""]

    return "\n".join(lines).rstrip() + "\n"


def _paragraph(video: Video, start: float, texts: list[str]) -> str:
    link = f"{video.url}&t={int(start)}s"
    return f"**[{format_timestamp(start)}]({link})** " + " ".join(texts)


def transcript_path(directory: Path, video: Video) -> Path:
    date = (video.published_at or datetime.now(timezone.utc).isoformat())[:10]
    slug = slugify(video.title) or "video"
    return directory / f"{date}_{slug}_{video.video_id}.md"


def save_markdown(directory: Path, video: Video, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = transcript_path(directory, video)
    path.write_text(content, encoding="utf-8")
    return path
