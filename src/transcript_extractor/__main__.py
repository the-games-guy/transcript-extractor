"""Entry point.

    transcript-extractor [serve]                 run the web app (default)
    transcript-extractor fetch <url> [<url>...]  save transcripts from the command line
    transcript-extractor <url> [<url>...]        same as fetch (the original CLI form)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .config import Settings
from . import writer
from .db import Database
from .pipeline import process_video
from .youtube import Video, YouTubeError, parse_video_id


def check_output_dir(settings: Settings) -> str | None:
    """Return an error message if notes can't be written, else None."""
    out = settings.output_dir
    if not out.is_dir():
        return f"output folder {out} does not exist"
    if not os.access(out, os.W_OK):
        return (f"cannot write to {out} as uid {os.getuid()}; "
                "check APP_UID/APP_GID in .env match the syncthing user")
    return None


def serve(settings: Settings) -> int:
    from waitress import serve as waitress_serve

    from .web import create_app

    app = create_app(settings)
    logging.info("Saving transcripts to %s", settings.output_dir)
    logging.info("Listening on http://%s:%s", settings.host, settings.port)
    if not settings.app_password:
        logging.warning("APP_PASSWORD is not set - anyone who can reach this port can use the app")
    try:
        waitress_serve(app, host=settings.host, port=settings.port, threads=8)
    finally:
        app.config["WORKER"].shutdown()
    return 0


def fetch(settings: Settings, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="transcript-extractor fetch")
    parser.add_argument("videos", nargs="+", help="YouTube URLs or video IDs")
    parser.add_argument("--force", action="store_true",
                        help="rewrite notes that already exist (discards nothing on other "
                             "devices, but replaces this folder's copy)")
    args = parser.parse_args(argv)

    db = Database(settings.db_path)
    failures = 0
    for raw in args.videos:
        try:
            video_id = parse_video_id(raw)
        except YouTubeError as exc:
            failures += 1
            print(f"error {raw}: {exc}", file=sys.stderr)
            continue
        skipped = not args.force and writer.find_existing(settings.output_dir, video_id)
        status = process_video(Video(video_id=video_id), settings, db, source="command line",
                               force=args.force)
        row = db.get_video(video_id)
        if status == "saved":
            print(f"{'skip ' if skipped else 'wrote'} {row['note_path']}"
                  + (" (already exists)" if skipped else ""))
        else:
            failures += 1
            print(f"error {raw}: {row['error']}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    for noisy in ("urllib3", "apscheduler.executors", "waitress.queue"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0

    settings = Settings.from_env()
    if error := check_output_dir(settings):
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not argv or argv[0] == "serve":
        return serve(settings)
    if argv[0] == "fetch":
        return fetch(settings, argv[1:])
    return fetch(settings, argv)  # original form: transcript-extractor <url> ...


if __name__ == "__main__":
    sys.exit(main())
