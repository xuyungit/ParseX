"""Deterministic ids (docs/v2_phase1_interfaces.md §2.1).

Ids come from positions and content digests, never from random numbers, so the
same input yields the same ids — the precondition for cache replay and
task-level reruns addressed by (block id, task, input hash).
"""

from __future__ import annotations

import re

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def block_id_pdf(page: int, seq: int) -> str:
    return f"b-p{page:03d}-{seq:04d}"


def block_id_docx(seq: int) -> str:
    return f"b-d{seq:05d}"


def observation_id(target: str, engine: str, n: int) -> str:
    """``target`` is a block id or a region id; ``n`` counts that engine's readings of the target."""
    return f"o-{target}-{engine}-{n}"


def relation_id(kind: str, src: str, dst: str) -> str:
    return f"r-{kind}-{src}-{dst}"


def asset_id(sha256: str) -> str:
    if not _SHA256_RE.match(sha256):
        raise ValueError(f"not a hex sha256 digest: {sha256!r}")
    return f"a-{sha256[:16]}"


def ledger_item_pdf(page: int, seq: int) -> str:
    return f"i-p{page:03d}-{seq:05d}"


def ledger_item_docx(seq: int) -> str:
    return f"i-d{seq:05d}"
