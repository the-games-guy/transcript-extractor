"""Remembers which videos have been processed so scheduled runs skip them."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class ProcessedStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def __contains__(self, video_id: str) -> bool:
        return video_id in self._data

    def get(self, video_id: str) -> dict | None:
        return self._data.get(video_id)

    def mark(self, video_id: str, **info) -> None:
        with self._lock:
            self._data[video_id] = {
                "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **{k: (str(v) if isinstance(v, Path) else v) for k, v in info.items()},
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
