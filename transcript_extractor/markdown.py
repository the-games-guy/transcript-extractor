"""Render a transcript as a markdown note."""

from __future__ import annotations

import json
from datetime import date

from .youtube import Transcript, Video

PARAGRAPH_SECONDS = 60


def _timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _yaml_str(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def render(video: Video, transcript: Transcript, fetched_on: date) -> str:
    lines = [
        "---",
        f"title: {_yaml_str(video.title)}",
        f"channel: {_yaml_str(video.channel)}",
        f"url: {_yaml_str(video.url)}",
        f"video_id: {_yaml_str(video.video_id)}",
        f"language: {_yaml_str(transcript.language_code)}",
        f"auto_generated: {'true' if transcript.is_generated else 'false'}",
        f"fetched: {fetched_on.isoformat()}",
        "type: youtube-transcript",
        "tags: [youtube, transcript]",
        "---",
        "",
        f"# {video.title}",
        "",
        f"[{video.channel or 'YouTube'}]({video.url})",
        "",
    ]

    # Group the short caption snippets into roughly minute-long paragraphs,
    # each starting with a timestamp link back to that point in the video.
    paragraph: list[str] = []
    para_start = 0.0
    for snip in transcript.snippets:
        text = " ".join(snip.text.split())
        if not text:
            continue
        if not paragraph:
            para_start = snip.start
        elif snip.start - para_start >= PARAGRAPH_SECONDS:
            lines += [_paragraph(video, para_start, paragraph), ""]
            paragraph, para_start = [], snip.start
        paragraph.append(text)
    if paragraph:
        lines += [_paragraph(video, para_start, paragraph), ""]

    return "\n".join(lines)


def _paragraph(video: Video, start: float, texts: list[str]) -> str:
    link = f"{video.url}&t={int(start)}s"
    return f"[{_timestamp(start)}]({link}) " + " ".join(texts)
