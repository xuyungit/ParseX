"""On-disk response cache (guide §8.3).

Layout: ``<root>/raw/<service>/<key[:2]>/<key>.json``; ``<root>/derived/`` is
reserved for post-processed results (Phase 1).  Writes go to a temp file and
are moved into place, so concurrent writers never leave a partial entry.

Modes:
- ``read_write``: replay hits, record misses (default for development);
- ``read_only``: offline replay — a miss raises ``CacheMiss``;
- ``refresh``: never read, always record (fresh real requests);
- ``off``: no cache (``open_cache`` returns None).

Retention (Q152): an entry's file time is when it was last used — written, or read back by a cache that may write
(a read-only replay of a frozen run changes nothing).  ``prune`` deletes the entries not used for ``keep_days``
days: those of a changed prompt, model or dependency are never read again and go after that long, entries still in
use stay.  ``parserx parse`` prunes its cache at most once a day (``maybe_prune``; ``cache.keep_days``, 90 by
default, 0 to keep everything — the evaluation configs keep theirs); ``parserx cache`` shows the size and prunes or
clears by hand.  The layout model under ``models/`` is not cache and is never touched.
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
        self.root = Path(root).expanduser()
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
        self._used(path)
        return True, entry["response"]

    def derived_path(self, kind: str, key: str) -> Path:
        return self.root / "derived" / kind / key[:2] / f"{key}.json"

    def get_derived(self, kind: str, key: str) -> tuple[bool, Any]:
        """A locally computed result (e.g. layout detections); never a network response."""
        path = self.derived_path(kind, key)
        if self.mode == "refresh" or not path.exists():
            return False, None
        value = json.loads(path.read_text(encoding="utf-8"))["value"]
        self._used(path)
        return True, value

    def _used(self, path: Path) -> None:
        """Mark *path* as used now (its file time), where this cache may write: what retention goes by."""
        if self.writable:
            try:
                os.utime(path)
            except OSError:
                pass

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


# ── Retention (Q152) ────────────────────────────────────────────────────

KINDS = ("raw", "derived", "jobs")  # what pruning and clearing touch: answers, local results, scan-engine jobs
_PRUNED = ".pruned"  # its file time: when the cache was last pruned
_DAY = 86400.0


def usage(root: Path | str) -> dict[str, tuple[int, int, float | None]]:
    """Kind → (files, bytes, the oldest last use as a timestamp); ``models`` too, for the whole picture."""
    root = Path(root).expanduser()
    out = {}
    for kind in (*KINDS, "models"):
        files = [f for f in (root / kind).rglob("*") if f.is_file()] if (root / kind).is_dir() else []
        stats = [f.stat() for f in files]
        out[kind] = (len(files), sum(st.st_size for st in stats), min((st.st_mtime for st in stats), default=None))
    return out


def prune(root: Path | str, keep_days: float, *, now: float | None = None) -> tuple[int, int]:
    """Delete the entries not used for *keep_days* days; (files, bytes) deleted."""
    root = Path(root).expanduser()
    limit = (now if now is not None else datetime.now(timezone.utc).timestamp()) - keep_days * _DAY
    return _remove(root, lambda st: st.st_mtime < limit)


def clear(root: Path | str) -> tuple[int, int]:
    """Delete every entry (the layout model stays); (files, bytes) deleted."""
    return _remove(Path(root).expanduser(), lambda st: True)


def maybe_prune(config: CacheConfig, *, now: float | None = None) -> tuple[int, int] | None:
    """Prune a cache that may write and keeps ``keep_days``, at most once a day; None when it was not due."""
    if config.mode not in ("read_write", "refresh") or config.keep_days <= 0:
        return None
    root = Path(config.dir).expanduser()
    if not root.is_dir():
        return None
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    marker = root / _PRUNED
    if marker.exists() and now - marker.stat().st_mtime < _DAY:
        return None
    done = prune(root, config.keep_days, now=now)
    marker.touch()
    os.utime(marker, (now, now))
    return done


def _remove(root: Path, wanted) -> tuple[int, int]:
    files = size = 0
    for kind in KINDS:
        base = root / kind
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), reverse=True):  # files before the directories holding them
            try:
                if path.is_file():
                    st = path.stat()
                    if wanted(st):
                        path.unlink()
                        files, size = files + 1, size + st.st_size
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:  # in use by another run, or gone already
                continue
    return files, size

