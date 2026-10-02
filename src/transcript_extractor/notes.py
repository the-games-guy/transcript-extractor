"""Rendering transcripts as Obsidian notes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import writer
from .transcripts import Transcript
from .youtube import Video

# The Claude routine looks for this exact line and replaces it with the summary.
SUMMARY_PLACEHOLDER = "<!-- summary:pending -->"

# Group transcript segments into paragraphs of roughly this many seconds.
PARAGRAPH_SECONDS = 60


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _yaml_str(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def render_note(
    video: Video,
    transcript: Transcript,
    *,
    source: str = "",
    tags: list[str] | None = None,
    fetched_on: date | None = None,
) -> str:
    fetched_on = fetched_on or date.today()
    title = video.title or video.video_id
    tags = tags if tags is not None else ["youtube", "transcript"]
    lines = [
        "---",
        f"title: {_yaml_str(title)}",
        f"channel: {_yaml_str(video.channel)}",
        f"url: {_yaml_str(video.url)}",
        f"video_id: {_yaml_str(video.video_id)}",
    ]
    if video.published_at:
        lines.append(f"published: {video.published_at[:10]}")
    lines += [
        f"language: {_yaml_str(transcript.language_code)}",
        f"auto_generated: {'true' if transcript.is_generated else 'false'}",
        f"fetched: {fetched_on.isoformat()}",
    ]
    if source:
        lines.append(f"source: {_yaml_str(source)}")
    lines += [
        # Marks the note as untrusted third-party text.
        "type: youtube-transcript",
        "summary_status: pending",
        "tags: [" + ", ".join(_yaml_str(t) if not t.isidentifier() else t for t in tags) + "]",
        "---",
        "",
        f"# {title}",
        "",
        f"![]({video.url})",
        "",
    ]
    meta = [f"[{video.channel or 'YouTube'}]({video.url})"]
    if video.published_at:
        meta.append(f"Published {video.published_at[:10]}")
    meta.append(transcript.language + (" (auto-generated)" if transcript.is_generated else ""))
    lines += [" · ".join(meta), "", "## Summary", "", SUMMARY_PLACEHOLDER, "",
              "## Transcript", ""]

    # Group the short caption snippets into roughly minute-long paragraphs,
    # each starting with a timestamp link back to that point in the video.
    paragraph: list[str] = []
    para_start = 0.0
    for seg in transcript.segments:
        text = " ".join(seg.text.split())
        if not text:
            continue
        if not paragraph:
            para_start = seg.start
        elif seg.start - para_start >= PARAGRAPH_SECONDS:
            lines += [_paragraph(video, para_start, paragraph), ""]
            paragraph, para_start = [], seg.start
        paragraph.append(text)
    if paragraph:
        lines += [_paragraph(video, para_start, paragraph), ""]
    return "\n".join(lines).rstrip() + "\n"


def _paragraph(video: Video, start: float, texts: list[str]) -> str:
    return f"[{format_timestamp(start)}]({video.url}&t={int(start)}s) " + " ".join(texts)


def write_note(folder: Path, video: Video, content: str, *, replace: Path | None = None) -> Path:
    """Write `<title> (<video_id>).md` atomically (Syncthing-safe).

    `replace` is an existing note for the same video; if the title changed, the
    old file is removed so the folder never holds two notes for one video.
    """
    path = folder / writer.safe_filename(video.title or video.video_id, video.video_id)
    writer.write_atomic(path, content)
    if replace and replace != path:
        replace.unlink(missing_ok=True)
    return path
