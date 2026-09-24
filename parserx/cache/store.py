"""On-disk response cache (guide §8.3).

Layout: ``<root>/raw/<service>/<key[:2]>/<key>.json``; ``<root>/derived/`` is
reserved for post-processed results (Phase 1).  Writes go to a temp file and
are moved into place, so concurrent writers never leave a partial entry.

Modes:
- ``read_write``: replay hits, record misses (default for development);
- ``read_only``: offline replay — a miss raises ``CacheMiss``;
- ``refresh``: never read, always record (fresh real requests);
- ``off``: no cache (``open_cache`` returns None).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from parserx.cache.keys import CACHE_SCHEMA_VERSION
from parserx.config.schema import CacheConfig

CacheMode = Literal["off", "read_write", "read_only", "refresh"]


class CacheMiss(RuntimeError):
    """Offline replay needed a response the cache does not hold."""

    def __init__(self, service: str, key: str):
        super().__init__(f"{service} response not cached (key {key[:12]}); rerun with calls allowed")
        self.service = service
        self.key = key


class ResponseCache:
    def __init__(self, root: Path | str, mode: CacheMode = "read_write"):
        if mode == "off":
            raise ValueError("use open_cache() for mode 'off'")
        self.root = Path(root)
        self.mode = mode

    @property
    def readable(self) -> bool:
        return self.mode in ("read_write", "read_only")

    @property
    def writable(self) -> bool:
        return self.mode in ("read_write", "refresh")

    @property
    def offline(self) -> bool:
        return self.mode == "read_only"

    def path(self, service: str, key: str) -> Path:
        return self.root / "raw" / service / key[:2] / f"{key}.json"

    def get(self, service: str, key: str) -> tuple[bool, Any]:
        """(hit, response)."""
        path = self.path(service, key)
        if not self.readable or not path.exists():
            return False, None
        entry = json.loads(path.read_text(encoding="utf-8"))
        return True, entry["response"]

    def derived_path(self, kind: str, key: str) -> Path:
        return self.root / "derived" / kind / key[:2] / f"{key}.json"

    def get_derived(self, kind: str, key: str) -> tuple[bool, Any]:
        """A locally computed result (e.g. layout detections); never a network response."""
        path = self.derived_path(kind, key)
        if self.mode == "refresh" or not path.exists():
            return False, None
        return True, json.loads(path.read_text(encoding="utf-8"))["value"]

    def put_derived(self, kind: str, key: str, value: Any) -> None:
        if self.mode == "read_only":
            return
        path = self.derived_path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"cache_schema": CACHE_SCHEMA_VERSION, "kind": kind, "key": key, "value": value}, handle,
                          ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put(self, service: str, key: str, response: Any, request: dict[str, Any]) -> None:
        if not self.writable:
            return
        path = self.path(service, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "cache_schema": CACHE_SCHEMA_VERSION,
            "service": service,
            "key": key,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": request,
            "response": response,
        }
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(entry, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def open_cache(config: CacheConfig) -> ResponseCache | None:
    if config.mode == "off":
        return None
    return ResponseCache(config.dir, config.mode)
