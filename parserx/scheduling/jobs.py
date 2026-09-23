"""Remember submitted OCR jobs so an interrupted run polls them instead of resubmitting (guide §8.2).

Keys are request keys (the same hash as the response cache), so a job is
reused only for exactly the same request.  Without a root directory the store
lives in memory and covers retries within one process.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path


class JobStore:
    def __init__(self, root: Path | str | None = None):
        self._root = Path(root) if root is not None else None
        self._memory: dict[str, str] = {}
        self._lock = threading.Lock()

    def _path(self, key: str) -> Path:
        assert self._root is not None
        return self._root / f"{key}.json"

    def get(self, key: str) -> str | None:
        with self._lock:
            if self._root is None:
                return self._memory.get(key)
            path = self._path(key)
            return json.loads(path.read_text(encoding="utf-8"))["job_id"] if path.exists() else None

    def put(self, key: str, job_id: str) -> None:
        with self._lock:
            if self._root is None:
                self._memory[key] = job_id
                return
            self._root.mkdir(parents=True, exist_ok=True)
            self._path(key).write_text(json.dumps({"job_id": job_id}), encoding="utf-8")

    def drop(self, key: str) -> None:
        with self._lock:
            if self._root is None:
                self._memory.pop(key, None)
            else:
                self._path(key).unlink(missing_ok=True)
