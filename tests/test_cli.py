from transcript_extractor import __main__ as cli
from transcript_extractor import extract as extract_mod
from transcript_extractor.youtube import Snippet, Transcript, Video


def test_cli_writes_then_skips(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        extract_mod.youtube, "fetch_video", lambda vid: Video(vid, "A title", "Chan")
    )
    monkeypatch.setattr(
        extract_mod.youtube,
        "fetch_transcript",
        lambda vid, langs: Transcript("English", "en", False, [Snippet(0, "hi")]),
    )
    args = ["--output", str(tmp_path), "https://youtu.be/dQw4w9WgXcQ"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0
    assert cli.main(["--output", str(tmp_path), "not a url"]) == 1
    out = capsys.readouterr()
    assert "wrote A title (dQw4w9WgXcQ).md" in out.out
    assert "skip  A title (dQw4w9WgXcQ).md (already exists)" in out.out
    assert "error not a url" in out.err


def test_cli_missing_output_dir(tmp_path, capsys):
    assert cli.main(["--output", str(tmp_path / "nope"), "dQw4w9WgXcQ"]) == 2
    assert "does not exist" in capsys.readouterr().err
