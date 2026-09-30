"""Rendering transcripts as Obsidian notes."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .transcripts import Transcript
from .youtube import Video

# The Claude routine looks for this exact line and replaces it with the summary.
SUMMARY_PLACEHOLDER = "<!-- summary:pending -->"

# Group transcript segments into paragraphs of roughly this many seconds.
PARAGRAPH_SECONDS = 60

# Characters Obsidian (or common filesystems) don't allow in note names.
_ILLEGAL = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')


def safe_title(title: str, max_length: int = 100) -> str:
    cleaned = _ILLEGAL.sub(" ", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned[:max_length].rstrip(" .") or "Untitled video"


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_note(
    video: Video,
    transcript: Transcript,
    *,
    source: str = "",
    tags: list[str] | None = None,
    saved_at: datetime | None = None,
) -> str:
    saved_at = saved_at or datetime.now(timezone.utc)
    title = video.title or video.video_id
    properties = {
        "title": title,
        "channel": video.channel or None,
        "url": video.url,
        "video_id": video.video_id,
        "published": video.published_at[:10] or None,
        "saved": saved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": transcript.language_code,
        "auto_generated": transcript.is_generated,
        "source": source or None,
        "summary_status": "pending",
        "tags": tags if tags is not None else ["youtube", "transcript"],
    }
    frontmatter = yaml.safe_dump(
        {k: v for k, v in properties.items() if v is not None},
        sort_keys=False, allow_unicode=True, width=10_000,
    )

    meta = []
    if video.channel:
        meta.append(f"**Channel:** {video.channel}")
    if video.published_at:
        meta.append(f"**Published:** {video.published_at[:10]}")
    meta.append(
        f"**Transcript:** {transcript.language}"
        + (" (auto-generated)" if transcript.is_generated else "")
    )

    lines = [
        "---", frontmatter.rstrip(), "---", "",
        f"# {title}", "",
        f"![]({video.url})", "",
        " · ".join(meta), "",
        "## Summary", "",
        SUMMARY_PLACEHOLDER, "",
        "## Transcript", "",
    ]
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
    return f"**[{format_timestamp(start)}]({video.url}&t={int(start)}s)** " + " ".join(texts)


def note_path(folder: Path, video: Video) -> Path:
    """`<folder>/<YYYY-MM-DD> <Title>.md`, disambiguated by video ID on a name clash."""
    date = (video.published_at or datetime.now(timezone.utc).isoformat())[:10]
    path = folder / f"{date} {safe_title(video.title or video.video_id)}.md"
    if path.exists() and video.video_id not in path.read_text(
        encoding="utf-8", errors="ignore"
    )[:2000]:
        path = path.with_name(f"{path.stem} ({video.video_id}).md")
    return path


def write_note(folder: Path, video: Video, content: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = note_path(folder, video)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
    return path
