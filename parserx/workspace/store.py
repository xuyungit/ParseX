"""Workspace persistence (docs/v2_phase1_interfaces.md §2.10).

Layout of ``<ws>/``:

- ``state.json``  — the ``DocumentState``; replaced atomically on each commit;
- ``source.<ext>`` — a copy of the input, so tools never depend on the original path;
- ``assets/``     — content-addressed images (``<asset id>.<ext>``);
- ``calls.jsonl`` — append-only log of committed transactions and tool calls (guide §7.5);
- ``.lock``       — ``flock`` target serialising writers across processes.

A transaction reads the latest state under the lock, lets the caller mutate
it, re-validates the whole state and writes it with ``version + 1``. Any
exception inside the block leaves ``state.json`` untouched.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from parserx.ir import ids
from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import Affine
from parserx.ir.state import DocumentState

_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/bmp": ".bmp",
               "image/tiff": ".tif", "image/webp": ".webp", "image/x-emf": ".emf", "image/x-wmf": ".wmf"}


class WorkspaceExists(FileExistsError):
    pass


class WorkspaceLocked(TimeoutError):
    pass


class VersionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int):
        super().__init__(f"workspace is at version {actual}, caller expected {expected}")
        self.expected = expected
        self.actual = actual


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._in_txn = False

    # ── Paths ───────────────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def calls_path(self) -> Path:
        return self.root / "calls.jsonl"

    @property
    def lock_path(self) -> Path:
        return self.root / ".lock"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def source_path(self) -> Path:
        matches = sorted(self.root.glob("source.*"))
        if len(matches) != 1:
            raise FileNotFoundError(f"workspace {self.root} has {len(matches)} source files")
        return matches[0]

    # ── Lifecycle ───────────────────────────────────────────────────────

    @classmethod
    def create(cls, root: Path | str, state: DocumentState, source_file: Path | str,
               *, timeout: float = 30.0) -> "Workspace":
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise WorkspaceExists(f"{root} is not empty")
        root.mkdir(parents=True, exist_ok=True)
        ws = cls(root)
        ws.assets_dir.mkdir()
        shutil.copyfile(source_file, root / f"source{Path(source_file).suffix.lower()}")
        with ws._locked(timeout):
            initial = DocumentState.model_validate({**state.model_dump(), "version": 1})
            ws._write_state(initial)
            ws._append({"type": "txn", "actor": "workspace:create", "version": initial.version})
        return ws

    @classmethod
    def open(cls, root: Path | str) -> "Workspace":
        ws = cls(root)
        if not ws.state_path.is_file():
            raise FileNotFoundError(f"no workspace at {root}")
        return ws

    def load(self) -> DocumentState:
        return DocumentState.model_validate_json(self.state_path.read_bytes())

    # ── Transactions ────────────────────────────────────────────────────

    @contextmanager
    def txn(self, actor: str, *, expect_version: int | None = None,
            timeout: float = 30.0) -> Iterator[DocumentState]:
        if self._in_txn:
            raise RuntimeError("nested workspace transaction")
        with self._locked(timeout):
            self._in_txn = True
            try:
                state = self.load()
                if expect_version is not None and state.version != expect_version:
                    raise VersionConflict(expect_version, state.version)
                yield state
                committed = DocumentState.model_validate({**state.model_dump(), "version": state.version + 1})
                self._write_state(committed)
                self._append({"type": "txn", "actor": actor, "version": committed.version})
            finally:
                self._in_txn = False

    def log_call(self, record: dict[str, Any]) -> None:
        self._append({"type": "call", **record})

    # ── Assets ──────────────────────────────────────────────────────────

    def add_asset(self, data: bytes, *, media_type: str, width: int, height: int, role: str,
                  derived_from: str | None = None, source: PdfAnchor | DocxAnchor | None = None,
                  transform: Affine | None = None, dpi: float | None = None) -> Asset:
        """Store *data* under its digest (idempotent) and return its Asset; the caller records it in a txn."""
        digest = hashlib.sha256(data).hexdigest()
        asset_id = ids.asset_id(digest)
        rel = f"assets/{asset_id}{_EXTENSIONS.get(media_type, '.bin')}"
        path = self.root / rel
        if not path.exists():
            _atomic_write(path, data)
        return Asset(id=asset_id, sha256=digest, path=rel, media_type=media_type, width=width, height=height,
                     role=role, derived_from=derived_from, source=source, transform=transform, dpi=dpi)

    # ── Internals ───────────────────────────────────────────────────────

    @contextmanager
    def _locked(self, timeout: float) -> Iterator[None]:
        with open(self.lock_path, "a") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise WorkspaceLocked(f"{self.root} is locked by another writer") from None
                    time.sleep(0.02)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write_state(self, state: DocumentState) -> None:
        _atomic_write(self.state_path, state.model_dump_json().encode("utf-8"))

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record},
                          ensure_ascii=False, default=str)
        with open(self.calls_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
