from transcript_extractor.emailer import EmailItem, build_message, markdown_to_html
from transcript_extractor.markdown import (
    format_timestamp, render_markdown, save_markdown, slugify,
)


def test_slugify_and_timestamp():
    assert slugify("Héllo, World! -- 2026") == "hello-world-2026"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3725) == "1:02:05"


def test_render_markdown(video, transcript):
    md = render_markdown(video, transcript, summary="**TL;DR** It's a song.")
    assert md.startswith("---\n")
    assert 'title: "Never \\"Gonna\\" Give You Up"' in md
    assert "## Summary\n\n**TL;DR** It's a song." in md
    # First two segments span >= 60s and form one paragraph; the third starts a new one.
    assert "**[00:00](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=0s)** We're no strangers" in md
    assert "**[01:05](https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=65s)** Never gonna" in md
    assert "(auto-generated)" in md


def test_save_markdown(tmp_path, video, transcript):
    path = save_markdown(tmp_path / "out", video, render_markdown(video, transcript))
    assert path.name == "2009-10-25_never-gonna-give-you-up_dQw4w9WgXcQ.md"
    assert "## Transcript" in path.read_text()


def test_markdown_to_html():
    html = markdown_to_html("## Key points\n- **Bold** item\n- <script>\n\nPara *em*")
    assert "<h4>Key points</h4>" in html
    assert "<li><strong>Bold</strong> item</li>" in html
    assert "&lt;script&gt;" in html
    assert "<p>Para <em>em</em></p>" in html


def test_build_message_digest():
    items = [EmailItem("A", "https://y/a", "Chan", "- one"), EmailItem("B", "https://y/b", "", "x")]
    msg = build_message(items, sender="me@x.com", recipients=["a@x.com", "b@x.com"])
    assert msg["Subject"] == "2 new video summaries"
    assert msg["To"] == "a@x.com, b@x.com"
    body = msg.get_body(("plain",)).get_content()
    assert "https://y/a" in body and "https://y/b" in body
    assert "<li>one</li>" in msg.get_body(("html",)).get_content()
