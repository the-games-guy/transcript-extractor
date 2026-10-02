from datetime import date

import yaml

from transcript_extractor import writer
from transcript_extractor.notes import (
    SUMMARY_PLACEHOLDER, format_timestamp, render_note, write_note,
)
from transcript_extractor.youtube import Video


def test_format_timestamp():
    assert format_timestamp(5) == "0:05"
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(3725) == "1:02:05"


def test_render_note_frontmatter_and_body(video, transcript):
    md = render_note(video, transcript, source="watch: music", tags=["youtube", "ai-news"],
                     fetched_on=date(2026, 10, 2))
    _, front, body = md.split("---\n", 2)
    props = yaml.safe_load(front)
    assert props["title"] == video.title  # quotes and colons survive
    assert props["video_id"] == "dQw4w9WgXcQ"
    assert props["published"] == date(2009, 10, 25)
    assert props["fetched"] == date(2026, 10, 2)
    assert props["source"] == "watch: music"
    assert props["type"] == "youtube-transcript"  # kept from the original CLI format
    assert props["summary_status"] == "pending"
    assert props["tags"] == ["youtube", "ai-news"]
    assert props["auto_generated"] is True
    assert f"## Summary\n\n{SUMMARY_PLACEHOLDER}\n\n## Transcript" in body
    assert "![](https://www.youtube.com/watch?v=dQw4w9WgXcQ)" in body
    # Snippets within 60s of a paragraph's start join it; blank snippets are dropped.
    assert ("[0:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s) "
            "We're no strangers to love You know the rules\n") in body
    assert "[1:02:05](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3725s) Never gonna" in body


def test_write_note_uses_original_naming_and_replaces_renamed(settings, video, transcript):
    out = settings.output_dir
    old = out / "Old title (dQw4w9WgXcQ).md"
    old.write_text("old")
    path = write_note(out, video, render_note(video, transcript), replace=old)
    assert path.name == "Never Gonna Give You Up (dQw4w9WgXcQ).md"
    assert path.name == writer.safe_filename(video.title, video.video_id)
    assert sorted(p.name for p in out.iterdir()) == [path.name]  # no temp files, old removed


def test_untitled_video_falls_back_to_id(settings, transcript):
    path = write_note(settings.output_dir, Video("abcdefghijk"),
                      render_note(Video("abcdefghijk"), transcript))
    assert path.name == "abcdefghijk (abcdefghijk).md"
