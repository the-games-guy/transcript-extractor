from datetime import date

import pytest

from transcript_extractor import markdown
from transcript_extractor.youtube import Snippet, Transcript, Video, parse_video_id


@pytest.mark.parametrize(
    "value",
    [
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=PL123",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/live/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
    ],
)
def test_parse_video_id(value):
    assert parse_video_id(value) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value", ["", "https://example.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/", "short"]
)
def test_parse_video_id_rejects(value):
    with pytest.raises(ValueError):
        parse_video_id(value)


def test_render():
    video = Video("dQw4w9WgXcQ", 'A "quoted" title', "Some Channel")
    transcript = Transcript(
        "English",
        "en",
        True,
        [
            Snippet(0.0, "hello\nthere"),
            Snippet(30.0, "  "),
            Snippet(59.9, "same paragraph"),
            Snippet(3725.0, "later"),
        ],
    )
    note = markdown.render(video, transcript, date(2026, 9, 30))
    assert note.startswith("---\ntitle: \"A \\\"quoted\\\" title\"\n")
    assert "auto_generated: true\n" in note
    assert "fetched: 2026-09-30\n" in note
    assert (
        "[0:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s) hello there same paragraph\n"
        in note
    )
    assert "[1:02:05](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3725s) later\n" in note
