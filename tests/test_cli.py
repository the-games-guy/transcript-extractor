import pytest

from transcript_extractor import __main__ as cli
from transcript_extractor import pipeline


@pytest.fixture
def env(monkeypatch, settings):
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls, env_file=None: settings))
    return settings


def test_original_cli_form_saves_and_skips(monkeypatch, env, transcript, capsys):
    monkeypatch.setattr(pipeline, "get_video", lambda vid, key: cli.Video(vid, "Song", "Rick"))
    monkeypatch.setattr(pipeline, "fetch_transcript", lambda vid, langs: transcript)

    # `docker compose run --rm yt-transcripts <url>` passes the URL straight through.
    assert cli.main(["https://youtu.be/dQw4w9WgXcQ"]) == 0
    assert "wrote Song (dQw4w9WgXcQ).md" in capsys.readouterr().out
    assert (env.output_dir / "Song (dQw4w9WgXcQ).md").exists()

    assert cli.main(["fetch", "dQw4w9WgXcQ"]) == 0
    assert "skip  Song (dQw4w9WgXcQ).md (already exists)" in capsys.readouterr().out


def test_cli_reports_bad_input(env, capsys):
    assert cli.main(["not-a-video"]) == 1
    assert "error not-a-video" in capsys.readouterr().err


def test_cli_checks_output_dir(env, capsys):
    env.output_dir = env.output_dir / "missing"
    assert cli.main(["dQw4w9WgXcQ"]) == 2
    assert "does not exist" in capsys.readouterr().err
