"""Relation: structure between blocks (guide §4.1)."""

from __future__ import annotations

from parserx.ir.base import IRModel
from parserx.ir.enums import RelationKind


class Relation(IRModel):
    id: str  # ids.relation_id(kind, src, dst)
    kind: RelationKind
    src: str  # Block id
    dst: str  # Block id
    confidence: float | None = None
