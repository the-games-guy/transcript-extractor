from types import SimpleNamespace

import pytest
import requests
from youtube_transcript_api import NoTranscriptFound, RequestBlocked, TranscriptsDisabled

from transcript_extractor import pipeline, summarizer
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


class FakeStream:
    def __init__(self, message, captured, kwargs):
        self.message = message
        captured.update(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def _client(message, captured):
    stream = lambda **kw: FakeStream(message, captured, kw)  # noqa: E731
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_summarize_request_and_text(video, transcript):
    captured = {}
    msg = SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="thinking", thinking=""),
        SimpleNamespace(type="text", text="**TL;DR** Song."),
    ])
    out = summarizer.summarize(video, transcript, model="claude-opus-5-5",
                               client=_client(msg, captured))
    assert out == "**TL;DR** Song."
    assert captured["model"] == "claude-opus-5-5"
    assert captured["thinking"] == {"type": "adaptive"}
    assert captured["fallbacks"] == "default"
    assert "We're no strangers to love" in captured["messages"][0]["content"]


def test_summarize_refusal(video, transcript):
    msg = SimpleNamespace(stop_reason="refusal", content=[])
    with pytest.raises(summarizer.SummaryError):
        summarizer.summarize(video, transcript, model="m", client=_client(msg, {}))


def test_process_video_keeps_transcript_when_summary_fails(monkeypatch, settings, video, transcript):
    monkeypatch.setattr(pipeline, "fetch_transcript", lambda vid, langs: transcript)

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(pipeline, "summarize", boom)
    result = pipeline.process_video(video, settings)
    assert result.ok and result.summary is None
    assert "api down" in result.error
    assert result.markdown_path.exists()
    assert "api down" in result.email_item().summary


def test_process_video_no_transcript(monkeypatch, settings, video):
    def missing(*a):
        raise TranscriptError("TranscriptsDisabled")

    monkeypatch.setattr(pipeline, "fetch_transcript", missing)
    result = pipeline.process_video(video, settings)
    assert not result.ok and result.error.startswith("Transcript unavailable")
    assert not result.retryable
