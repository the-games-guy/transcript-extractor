import pytest

from transcript_extractor.config import Settings
from transcript_extractor.db import Database
from transcript_extractor.transcripts import Segment, Transcript
from transcript_extractor.youtube import Video


@pytest.fixture
def settings(tmp_path):
    return Settings(
        vault_dir=tmp_path / "vault",
        notes_folder="YouTube",
        data_dir=tmp_path / "data",
        youtube_api_key="yt-key",
        obsidian_vault_name="My Vault",
        secret_key="test",
    )


@pytest.fixture
def db(settings):
    return Database(settings.db_path)


@pytest.fixture
def video():
    return Video(
        video_id="dQw4w9WgXcQ",
        title='Never "Gonna": Give You Up?',
        channel="Rick Astley",
        published_at="2009-10-25T06:57:33Z",
    )


@pytest.fixture
def transcript():
    return Transcript(
        video_id="dQw4w9WgXcQ",
        language="English",
        language_code="en",
        is_generated=True,
        segments=[
            Segment(0.0, 30.0, "We're no strangers to love"),
            Segment(30.0, 35.0, "You know the rules and so do I"),
            Segment(65.0, 10.0, "Never gonna give you up"),
        ],
    )
