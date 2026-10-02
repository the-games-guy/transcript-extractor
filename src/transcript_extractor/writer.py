"""Write markdown into a folder that Syncthing watches.

Syncthing can pick up a file while it is still being written and send the
half-written copy to every device. So each note is written to a hidden
temporary file in the same folder, flushed to disk, and then renamed into
place in one step. The CT102 .stignore ignores the temporary prefix.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# Must match the ignore pattern in /srv/yt-transcripts/.stignore on CT102.
TEMP_PREFIX = ".tmp-"

# Characters that are illegal or awkward on Windows, macOS, iOS or Android.
_UNSAFE = re.compile(r'[<>:"/\\|?*#^\[\]\x00-\x1f\x7f]')
_SPACES = re.compile(r"\s+")
MAX_TITLE_CHARS = 120


def safe_filename(title: str, video_id: str) -> str:
    """Return '<title> (<video_id>).md', safe on every synced device.

    '#', '^', '[' and ']' are also removed because Obsidian treats them
    specially in note names and links.
    """
    cleaned = _SPACES.sub(" ", _UNSAFE.sub(" ", title)).strip()
    cleaned = cleaned[:MAX_TITLE_CHARS].strip()
    # No hidden files, and no trailing dots or spaces (invalid on Windows).
    cleaned = cleaned.lstrip(".").rstrip(". ")
    if not cleaned:
        cleaned = "Untitled"
    return f"{cleaned} ({video_id}).md"


def find_existing(output_dir: Path, video_id: str) -> Path | None:
    """Return a note already written for this video, even if its title changed."""
    suffix = f"({video_id}).md"
    for path in output_dir.iterdir():
        if path.name.endswith(suffix) and not path.name.startswith(TEMP_PREFIX):
            return path
    return None


def write_atomic(path: Path, content: str) -> None:
    """Write content to path so that readers see either nothing or the whole file."""
    fd, tmp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".md", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        # mkstemp creates 0600; notes should be readable like normal files.
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
