"""Workspace persistence (docs/v2_phase1_interfaces.md §2.10).

Layout of ``<ws>/``:

- ``state.json``  — the ``DocumentState``; replaced atomically on each commit;
- ``source.<ext>`` — a copy of the input, so tools never depend on the original path;
- ``assets/``     — content-addressed images (``<asset id>.<ext>``);
- ``calls.jsonl`` — append-only log of committed transactions and tool calls (guide §7.5);
- ``head.json``   — version and SHA-256 of the last committed ``state.json`` (plan P2-1);
- ``.lock``       — ``flock`` target serialising writers across processes.

A transaction reads the latest state under the lock, lets the caller mutate
it, re-validates the whole state and writes it with ``version + 1``. Any
exception inside the block leaves ``state.json`` untouched.

Integrity (plan P2-1): each commit records the digest of the bytes it wrote,
in ``head.json`` and in its ``txn`` record; a transaction refuses to start when
``state.json`` no longer matches (``WorkspaceTampered``), so a change made
outside the tools cannot be carried forward by a later commit.  Each call
record claims the transactions its ``Workspace`` instance committed since the
previous call record (``txns``), which lets ``verify_workspace`` find commits
made by anything but a tool call.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import threading
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import Affine
from parserx.ir.state import DocumentState

class WorkspaceExists(FileExistsError):
    pass


class WorkspaceLocked(TimeoutError):
    pass


class WorkspaceTampered(RuntimeError):
    """``state.json`` differs from what the last transaction committed."""


class VersionConflict(RuntimeError):
    def __init__(self, expected: int, actual: int):
        super().__init__(f"workspace is at version {actual}, caller expected {expected}")
        self.expected = expected
        self.actual = actual


class Workspace:
    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._in_txn = False
        self._unclaimed: list[int] = []  # versions committed here and not yet claimed by a call record

    # ── Paths ───────────────────────────────────────────────────────────

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    @property
    def calls_path(self) -> Path:
        return self.root / "calls.jsonl"

    @property
    def head_path(self) -> Path:
        return self.root / "head.json"

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
               *, files: dict[str, bytes] | None = None, timeout: float = 30.0) -> "Workspace":
        """New workspace; *files* (paths relative to the root, e.g. asset bytes) are written before the state."""
        root = Path(root)
        if root.exists() and any(root.iterdir()):
            raise WorkspaceExists(f"{root} is not empty")
        root.mkdir(parents=True, exist_ok=True)
        ws = cls(root)
        ws.assets_dir.mkdir()
        shutil.copyfile(source_file, root / f"source{Path(source_file).suffix.lower()}")
        for rel, data in sorted((files or {}).items()):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, data)
        with ws._locked(timeout):
            initial = DocumentState.model_validate({**state.model_dump(), "version": 1})
            ws._commit(initial, "workspace:create")
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
                problem = self._tampered()
                if problem is not None:
                    raise WorkspaceTampered(problem)
                state = self.load()
                if expect_version is not None and state.version != expect_version:
                    raise VersionConflict(expect_version, state.version)
                yield state
                committed = DocumentState.model_validate({**state.model_dump(), "version": state.version + 1})
                self._commit(committed, actor)
            finally:
                self._in_txn = False

    def log_call(self, record: dict[str, Any]) -> None:
        """Append a call record; it claims the transactions this instance committed since the last one."""
        self._append({"type": "call", **record, "txns": self._unclaimed})
        self._unclaimed = []

    # ── Integrity ───────────────────────────────────────────────────────

    def tampered(self, timeout: float = 30.0) -> str | None:
        """Why ``state.json`` is not what the last transaction committed, or None."""
        with self._locked(timeout):
            return self._tampered()

    def _tampered(self) -> str | None:
        if not self.head_path.is_file():
            # Workspaces from before head.json have no digest to compare against.
            if any(r.get("state_sha256") for r in read_records(self.calls_path) if r.get("type") == "txn"):
                return "head.json is missing"
            return None
        head = json.loads(self.head_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(self.state_path.read_bytes()).hexdigest()
        if digest != head.get("state_sha256"):
            return (f"state.json (sha256 {digest[:12]}) is not what transaction version {head.get('version')} "
                    f"committed ({str(head.get('state_sha256'))[:12]}): it was changed outside the tools")
        return None

    # ── Assets ──────────────────────────────────────────────────────────

    def add_asset(self, data: bytes, *, media_type: str, width: int, height: int, role: str,
                  derived_from: str | None = None, source: PdfAnchor | DocxAnchor | None = None,
                  transform: Affine | None = None, dpi: float | None = None) -> Asset:
        """Store *data* under its digest (idempotent) and return its Asset; the caller records it in a txn."""
        asset = Asset.from_bytes(data, media_type=media_type, width=width, height=height, role=role,
                                 derived_from=derived_from, source=source, transform=transform, dpi=dpi)
        path = self.root / asset.path
        if not path.exists():
            _atomic_write(path, data)
        return asset

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

    def _commit(self, state: DocumentState, actor: str) -> None:
        data = state.model_dump_json().encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        _atomic_write(self.state_path, data)
        _atomic_write(self.head_path, json.dumps({"version": state.version, "state_sha256": digest}).encode("utf-8"))
        self._append({"type": "txn", "actor": actor, "version": state.version, "state_sha256": digest})
        self._unclaimed.append(state.version)

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **record},
                          ensure_ascii=False, default=str)
        with _APPENDING, open(self.calls_path, "a", encoding="utf-8") as handle:  # calls may run side by side
            handle.write(line + "\n")


_APPENDING = threading.Lock()  # one line at a time in a calls.jsonl, whatever thread writes it


def read_records(path: Path) -> list[dict[str, Any]]:
    """The records of a ``calls.jsonl`` (empty when it does not exist)."""
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
