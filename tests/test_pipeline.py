from types import SimpleNamespace

import pytest
import requests
from youtube_transcript_api import NoTranscriptFound, RequestBlocked, TranscriptsDisabled

from transcript_extractor import pipeline
from transcript_extractor.pipeline import MAX_ATTEMPTS, needs_processing
from transcript_extractor.transcripts import TranscriptError, fetch_transcript


def _fetched(video_id="abcdefghijk", code="en"):
    return SimpleNamespace(
        video_id=video_id, language="English", language_code=code, is_generated=False,
        snippets=[SimpleNamespace(start=0.0, duration=2.0, text="hello\nworld"),
                  SimpleNamespace(start=2.0, duration=1.0, text="  ")],
    )


class FakeApi:
    def __init__(self, fetch_exc=None, listed=()):
        self.fetch_exc, self.listed = fetch_exc, listed

    def fetch(self, video_id, languages):
        if self.fetch_exc:
            raise self.fetch_exc
        return _fetched(video_id)

    def list(self, video_id):
        return self.listed


def test_fetch_transcript_preferred():
    t = fetch_transcript("abcdefghijk", ["en"], api=FakeApi())
    assert t.text == "hello world"  # newlines flattened, blank segments dropped


def test_fetch_transcript_falls_back_and_translates():
    translated = SimpleNamespace(fetch=lambda: _fetched(code="en"))
    german = SimpleNamespace(
        is_translatable=True,
        translation_languages=[SimpleNamespace(language_code="en", language="English")],
        translate=lambda code: translated,
        fetch=lambda: _fetched(code="de"),
    )
    api = FakeApi(fetch_exc=NoTranscriptFound("abcdefghijk", ["en"], []), listed=[german])
    assert fetch_transcript("abcdefghijk", ["en"], api=api).language_code == "en"


def test_fetch_transcript_disabled_is_permanent():
    with pytest.raises(TranscriptError, match="TranscriptsDisabled") as err:
        fetch_transcript("abcdefghijk", api=FakeApi(fetch_exc=TranscriptsDisabled("abcdefghijk")))
    assert err.value.permanent


@pytest.mark.parametrize("exc", [RequestBlocked("abcdefghijk"), requests.ConnectionError("down")])
def test_fetch_transcript_network_errors_are_transient(exc):
    with pytest.raises(TranscriptError) as err:
        fetch_transcript("abcdefghijk", api=FakeApi(fetch_exc=exc))
    assert not err.value.permanent


def test_needs_processing():
    assert needs_processing(None)
    assert not needs_processing({"status": "saved", "attempts": 1})
    assert not needs_processing({"status": "no_transcript", "attempts": 1})
    assert needs_processing({"status": "failed", "attempts": 1})
    assert not needs_processing({"status": "failed", "attempts": MAX_ATTEMPTS})


def test_process_video_saves_note(monkeypatch, settings, db, video, transcript):
    monkeypatch.setattr(pipeline, "fetch_transcript", lambda vid, langs: transcript)
    assert pipeline.process_video(video, settings, db, source="manual") == "saved"
    row = db.get_video(video.video_id)
    assert row["status"] == "saved" and row["attempts"] == 1 and row["source"] == "manual"
    assert row["note_path"] == "Never Gonna Give You Up (dQw4w9WgXcQ).md"
    note = (settings.output_dir / row["note_path"]).read_text()
    assert 'source: "manual"' in note and "summary_status: pending" in note


def test_process_video_keeps_existing_note(monkeypatch, settings, db, video):
    """A note from the original CLI (or one already summarised) is never overwritten."""
    existing = settings.output_dir / "Older title (dQw4w9WgXcQ).md"
    existing.write_text("summarised already")

    def should_not_fetch(*a):
        raise AssertionError("fetched a video that already has a note")

    monkeypatch.setattr(pipeline, "fetch_transcript", should_not_fetch)
    assert pipeline.process_video(video, settings, db, source="watch: w") == "saved"
    assert existing.read_text() == "summarised already"
    assert db.get_video(video.video_id)["note_path"] == existing.name

    # Pasted links have no title yet; it's recovered from the existing file name.
    pipeline.process_video(pipeline.Video("dQw4w9WgXcQ"), settings, db)
    assert db.get_video("dQw4w9WgXcQ")["title"] == "Older title"


def test_process_video_force_rewrites_and_renames(monkeypatch, settings, db, video, transcript):
    existing = settings.output_dir / "Older title (dQw4w9WgXcQ).md"
    existing.write_text("old")
    monkeypatch.setattr(pipeline, "fetch_transcript", lambda vid, langs: transcript)
    assert pipeline.process_video(video, settings, db, force=True) == "saved"
    assert [p.name for p in settings.output_dir.iterdir()] == [
        "Never Gonna Give You Up (dQw4w9WgXcQ).md"]


def test_process_video_missing_output_dir(settings, db, video):
    settings.output_dir = settings.output_dir / "missing"
    assert pipeline.process_video(video, settings, db) == "failed"
    assert "Output folder unavailable" in db.get_video(video.video_id)["error"]


@pytest.mark.parametrize("permanent,status", [(True, "no_transcript"), (False, "failed")])
def test_process_video_records_failures(monkeypatch, settings, db, video, permanent, status):
    def missing(*a):
        raise TranscriptError("nope", permanent=permanent)

    monkeypatch.setattr(pipeline, "fetch_transcript", missing)
    assert pipeline.process_video(video, settings, db) == status
    row = db.get_video(video.video_id)
    assert row["status"] == status and row["error"] == "nope" and row["note_path"] is None
