import yaml

from transcript_extractor.notes import (
    SUMMARY_PLACEHOLDER, format_timestamp, note_path, render_note, safe_title, write_note,
)
from transcript_extractor.youtube import Video


def test_safe_title_and_timestamp():
    assert safe_title('What is "AI"? Part 1/2 #shorts [live]') == "What is AI Part 1 2 shorts live"
    assert safe_title("...") == "Untitled video"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3725) == "1:02:05"


def test_render_note_frontmatter_and_body(video, transcript):
    md = render_note(video, transcript, source="watch: music", tags=["youtube", "music"])
    _, front, body = md.split("---\n", 2)
    props = yaml.safe_load(front)
    assert props["title"] == video.title  # quoting handled by YAML
    assert props["summary_status"] == "pending"
    assert props["video_id"] == "dQw4w9WgXcQ"
    assert props["published"] == "2009-10-25"
    assert props["source"] == "watch: music"
    assert props["tags"] == ["youtube", "music"]
    assert props["auto_generated"] is True
    assert f"## Summary\n\n{SUMMARY_PLACEHOLDER}\n\n## Transcript" in body
    assert "![](https://www.youtube.com/watch?v=dQw4w9WgXcQ)" in body
    # First two segments span >= 60s and form one paragraph; the third starts a new one.
    assert "**[00:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s)** We're no strangers" in body
    assert "**[01:05](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=65s)** Never gonna" in body


def test_write_note_and_name_clash(tmp_path, video, transcript):
    path = write_note(tmp_path, video, render_note(video, transcript))
    assert path.name == "2009-10-25 Never Gonna Give You Up.md"
    # Same video again -> same file.
    assert note_path(tmp_path, video) == path
    # Different video with the same title and date -> disambiguated.
    other = Video("zzzzzzzzzzz", video.title, published_at=video.published_at)
    assert note_path(tmp_path, other).name == "2009-10-25 Never Gonna Give You Up (zzzzzzzzzzz).md"
