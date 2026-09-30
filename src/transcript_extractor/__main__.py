"""Start the web app: `python -m transcript_extractor` (or `transcript-extractor`)."""

from __future__ import annotations

import logging
import sys

from waitress import serve

from .config import Settings
from .web import create_app


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    for noisy in ("urllib3", "apscheduler.executors", "waitress.queue"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = Settings.from_env()
    settings.notes_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings)
    logging.info("Saving transcripts to %s", settings.notes_dir.resolve())
    logging.info("Open http://%s:%s", settings.host, settings.port)
    if not settings.app_password and settings.host not in ("127.0.0.1", "localhost"):
        logging.warning("APP_PASSWORD is not set - anyone who can reach this port can use the app")
    try:
        serve(app, host=settings.host, port=settings.port, threads=8)
    finally:
        app.config["WORKER"].shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
