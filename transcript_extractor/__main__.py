"""Command line entry point.

    python -m transcript_extractor <url-or-id> [<url-or-id> ...]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApiException

from . import markdown, writer, youtube


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transcript-extractor")
    parser.add_argument("videos", nargs="+", help="YouTube URLs or video IDs")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.environ.get("OUTPUT_DIR", "/output")),
        help="folder to write notes into (default: $OUTPUT_DIR or /output)",
    )
    parser.add_argument(
        "--languages",
        default=os.environ.get("TRANSCRIPT_LANGUAGES", "en"),
        help="comma-separated preferred languages, in order (default: en)",
    )
    parser.add_argument(
        "--force", action="store_true", help="rewrite notes that already exist"
    )
    args = parser.parse_args(argv)

    if not args.output.is_dir():
        print(f"error: output folder {args.output} does not exist", file=sys.stderr)
        return 2
    if not os.access(args.output, os.W_OK):
        print(
            f"error: cannot write to {args.output} as uid {os.getuid()}; "
            "check APP_UID/APP_GID in .env match the syncthing user",
            file=sys.stderr,
        )
        return 2

    languages = [lang.strip() for lang in args.languages.split(",") if lang.strip()]
    failures = 0
    for raw in args.videos:
        try:
            video_id = youtube.parse_video_id(raw)
            existing = writer.find_existing(args.output, video_id)
            if existing and not args.force:
                print(f"skip  {existing.name} (already exists)")
                continue
            video = youtube.fetch_video(video_id)
            transcript = youtube.fetch_transcript(video_id, languages)
            note = markdown.render(video, transcript, date.today())
            path = args.output / writer.safe_filename(video.title, video_id)
            writer.write_atomic(path, note)
            # A renamed video would otherwise leave the old note behind.
            if existing and existing != path:
                existing.unlink()
            print(f"wrote {path.name}")
        except (ValueError, OSError, YouTubeTranscriptApiException) as exc:
            failures += 1
            print(f"error {raw}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
