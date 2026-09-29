"""Command-line interface: `yt-transcript`."""

from __future__ import annotations

import argparse
import logging
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import ConfigError, Settings
from .emailer import send_email
from .pipeline import ProcessResult, process_video
from .scheduler import load_watches, run_scheduler, run_watch
from .state import ProcessedStore
from .youtube import YouTubeError, parse_video_id, search_videos


def _report(results: list[ProcessResult]) -> None:
    for r in results:
        title = r.video.title or r.video.video_id
        if r.ok:
            print(f"  saved  {r.markdown_path}  ({title})")
            if r.error:
                print(f"         warning: {r.error}")
        else:
            print(f"  failed {r.video.url}  ({title}): {r.error}")


def _email_results(args, settings: Settings, results: list[ProcessResult]) -> None:
    ok = [r for r in results if r.ok]
    if args.no_email or not ok:
        return
    try:
        send_email(settings, [r.email_item() for r in ok], recipients=args.to or None)
    except ConfigError as exc:
        print(f"Skipping email: {exc}", file=sys.stderr)
        return
    except (smtplib.SMTPException, OSError) as exc:
        print(f"Email failed: {exc}", file=sys.stderr)
        return
    print(f"Emailed {len(ok)} summar{'y' if len(ok) == 1 else 'ies'} to "
          f"{', '.join(args.to or settings.email_to)}")


def cmd_process(args, settings: Settings) -> int:
    video_ids = [parse_video_id(u) for u in args.urls]
    results = [process_video(v, settings, with_summary=not args.no_summary) for v in video_ids]
    _report(results)
    _email_results(args, settings, results)
    return 0 if all(r.ok for r in results) else 1


def cmd_search(args, settings: Settings) -> int:
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    )
    videos = search_videos(
        args.query,
        settings.require_youtube_key(),
        max_results=args.max,
        published_after=published_after,
        order=args.order,
        channel_id=args.channel,
    )
    if not videos:
        print("No videos found.")
        return 0
    for i, v in enumerate(videos, 1):
        print(f"{i:>2}. {v.title}\n    {v.channel} | {v.published_at[:10]} | {v.url}")
    if not args.process:
        return 0

    print(f"\nProcessing {len(videos)} video(s)...")
    results = [process_video(v, settings, with_summary=not args.no_summary) for v in videos]
    _report(results)
    _email_results(args, settings, results)
    return 0 if all(r.ok for r in results) else 1


def cmd_watch_list(args, settings: Settings) -> int:
    for w in load_watches(args.config):
        schedule = f"every {w.every}" if w.every else f"cron '{w.cron}' ({w.timezone})"
        print(f"{w.name}: {w.query!r} - {schedule}, max {w.max_results}, lookback {w.lookback}")
    return 0


def cmd_watch_run(args, settings: Settings) -> int:
    watches = load_watches(args.config)
    if args.name:
        watches = [w for w in watches if w.name in args.name]
        if not watches:
            raise ConfigError(f"No watch named {', '.join(args.name)}")
    if args.once:
        store = ProcessedStore(settings.state_file)
        failed = False
        for w in watches:
            try:
                _report(run_watch(w, settings, store, send=not args.no_email))
            except (YouTubeError, ConfigError) as exc:
                print(f"[{w.name}] {exc}", file=sys.stderr)
                failed = True
        return 1 if failed else 0
    run_scheduler(watches, settings, send=not args.no_email)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-transcript",
        description="Save YouTube transcripts as Markdown and email Claude-written summaries.",
    )
    parser.add_argument("--env-file", help="Path to a .env file (default: ./.env)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_output_flags(p):
        p.add_argument("--no-summary", action="store_true", help="Skip the Claude summary")
        p.add_argument("--no-email", action="store_true", help="Don't send an email")
        p.add_argument("--to", action="append", metavar="EMAIL",
                       help="Recipient (repeatable; defaults to EMAIL_TO)")

    p = sub.add_parser("process", help="Process one or more YouTube URLs")
    p.add_argument("urls", nargs="+", metavar="URL", help="YouTube URL or video ID")
    add_output_flags(p)
    p.set_defaults(func=cmd_process)

    p = sub.add_parser("search", help="Search YouTube by keyword")
    p.add_argument("query")
    p.add_argument("-n", "--max", type=int, default=10, help="Max results (1-50)")
    p.add_argument("--days", type=int, help="Only videos from the last N days")
    p.add_argument("--order", default="relevance",
                   choices=["relevance", "date", "viewCount", "rating"])
    p.add_argument("--channel", help="Restrict to a channel ID")
    p.add_argument("--process", action="store_true",
                   help="Also fetch, save, summarize, and email every result")
    add_output_flags(p)
    p.set_defaults(func=cmd_search)

    watch = sub.add_parser("watch", help="Scheduled keyword lookups")
    wsub = watch.add_subparsers(dest="watch_command", required=True)
    for name, func, help_ in [
        ("list", cmd_watch_list, "Show configured watches"),
        ("run", cmd_watch_run, "Run the scheduler (or a single pass with --once)"),
    ]:
        wp = wsub.add_parser(name, help=help_)
        wp.add_argument("-c", "--config", type=Path, default=Path("watches.yaml"))
        wp.set_defaults(func=func)
        if name == "run":
            wp.add_argument("--once", action="store_true",
                            help="Run every watch once and exit (for cron / CI)")
            wp.add_argument("--name", action="append", help="Only run this watch (repeatable)")
            wp.add_argument("--no-email", action="store_true", help="Don't send emails")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "urllib3", "apscheduler.executors"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = Settings.from_env(args.env_file)
    try:
        return args.func(args, settings)
    except (ConfigError, YouTubeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
