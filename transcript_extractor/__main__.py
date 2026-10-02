"""Command line entry point.

    python -m transcript_extractor <url-or-id> [<url-or-id> ...]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .extract import check_output_dir, extract, parse_languages


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

    problem = check_output_dir(args.output)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    languages = parse_languages(args.languages)
    failures = 0
    for raw in args.videos:
        result = extract(raw, args.output, languages, args.force)
        if result.status == "skip":
            print(f"skip  {result.message} (already exists)")
        elif result.status == "wrote":
            print(f"wrote {result.message}")
        else:
            failures += 1
            print(f"error {raw}: {result.message}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
