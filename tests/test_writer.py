import os
import stat

import pytest

from transcript_extractor import writer


@pytest.mark.parametrize(
    "title, expected",
    [
        ("Plain title", "Plain title (abcdefghijk).md"),
        ('What is "AI"? A/B tests: part 1|2', "What is AI A B tests part 1 2 (abcdefghijk).md"),
        ("  ...hidden?  ", "hidden (abcdefghijk).md"),
        ("Ends with dots...", "Ends with dots (abcdefghijk).md"),
        ("#tag [link] ^block", "tag link block (abcdefghijk).md"),
        ("line\nbreak\ttab", "line break tab (abcdefghijk).md"),
        ("???", "Untitled (abcdefghijk).md"),
        ("Café 日本語 🎉", "Café 日本語 🎉 (abcdefghijk).md"),
    ],
)
def test_safe_filename(title, expected):
    assert writer.safe_filename(title, "abcdefghijk") == expected


def test_safe_filename_truncates_long_titles():
    name = writer.safe_filename("x" * 500, "abcdefghijk")
    assert name == "x" * writer.MAX_TITLE_CHARS + " (abcdefghijk).md"


def test_write_atomic_leaves_no_temp_files(tmp_path):
    target = tmp_path / "note.md"
    writer.write_atomic(target, "hello\n")
    assert target.read_text(encoding="utf-8") == "hello\n"
    assert os.listdir(tmp_path) == ["note.md"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_write_atomic_replaces_existing(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("old")
    writer.write_atomic(target, "new")
    assert target.read_text() == "new"


def test_write_atomic_cleans_up_on_failure(tmp_path, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(writer.os, "replace", boom)
    with pytest.raises(OSError):
        writer.write_atomic(tmp_path / "note.md", "data")
    assert os.listdir(tmp_path) == []


def test_find_existing_matches_video_id_and_ignores_temp(tmp_path):
    (tmp_path / "Old title (abcdefghijk).md").write_text("x")
    (tmp_path / ".tmp-123(zzzzzzzzzzz).md").write_text("x")
    assert writer.find_existing(tmp_path, "abcdefghijk").name == "Old title (abcdefghijk).md"
    assert writer.find_existing(tmp_path, "zzzzzzzzzzz") is None
