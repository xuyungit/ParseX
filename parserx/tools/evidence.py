"""Evidence in the workspace (Q85): recording what was seen in the source, and checking a change against it.

A change that rests on the source cites its evidence by id (or by the image it holds).  The check is the same
whatever looked: the evidence must show the changed block, a page it is on, the image it was read from, or — for a
table merged across pages — one of its parts; text added at a place needs an image of that place.
"""

from __future__ import annotations

from parserx.content.select import GateCheck
from parserx.ir.anchor import PdfAnchor
from parserx.ir.enums import BlockStatus, RelationKind
from parserx.ir.evidence import Evidence
from parserx.ir.state import DocumentState
from parserx.workspace.queries import block_unit


def record(state: DocumentState, evidence: Evidence) -> Evidence:
    """Add *evidence* to the state unless the same look is there already; returns the one in the state."""
    for known in state.evidence:
        if known.id == evidence.id:
            return known
    state.evidence.append(evidence)
    return evidence


def cited(state: DocumentState, ref: str) -> list[Evidence]:
    """The evidence *ref* names: an evidence id, or the image a look produced."""
    return [e for e in state.evidence if ref in (e.id, e.image)]


def image_evidence(state: DocumentState, block, ref: str) -> GateCheck:
    """*ref* shows *block*: its own image, the image it was read from, a part merged into it, or a whole page it is
    on (a page, or a seam across two pages)."""
    pages = {a.page for a in block.anchors if isinstance(a, PdfAnchor)} or {block_unit(state, block)}
    containers = {r.src for r in state.relations if r.kind == RelationKind.CONTAINS and r.dst == block.id}
    parts = _merged_parts(state, block.id)
    for evidence in cited(state, ref):
        if evidence.block == block.id:
            return _passed(f"{evidence.id} shows this block")
        if evidence.block in containers:
            return _passed(f"{evidence.id} shows the image this block was read from")
        if evidence.block in parts:
            return _passed(f"{evidence.id} shows {evidence.block}, merged into this block")
        shown = _whole_pages(evidence)
        if shown & pages:
            return _passed(f"{evidence.id} shows page {min(shown & pages)}")
    return _no_evidence(ref)


def image_evidence_at(state: DocumentState, page: int, bbox, ref: str) -> GateCheck:
    """*ref* shows the place (*page*, *bbox*): the whole page, or a block there that overlaps the place."""
    blocks = {b.id: b for b in state.blocks}
    for evidence in cited(state, ref):
        if page in _whole_pages(evidence):
            return _passed(f"{evidence.id} shows page {page}")
        block = blocks.get(evidence.block)
        if block is not None and any(isinstance(a, PdfAnchor) and a.page == page and _overlap(a.bbox, bbox)
                                     for a in block.anchors):
            return _passed(f"{evidence.id} shows {evidence.block}, at this place")
    return _no_evidence(ref)


def _whole_pages(evidence: Evidence) -> set[int]:
    if evidence.seam is not None:  # the seam image shows both pages around the break
        return {evidence.seam, evidence.seam + 1}
    return {evidence.page} if evidence.page is not None and evidence.block is None else set()


def _merged_parts(state: DocumentState, block_id: str) -> set[str]:
    """Blocks merged into *block_id* (continuations of a table across pages, followed through chains)."""
    merged = {b.id for b in state.blocks if b.status == BlockStatus.MERGED}
    following: dict[str, list[str]] = {}
    for r in state.relations:
        if r.kind == RelationKind.CONTINUES and r.dst in merged:
            following.setdefault(r.src, []).append(r.dst)
    parts, todo = set(), [block_id]
    while todo:
        for part in following.get(todo.pop(), []):
            if part not in parts:
                parts.add(part)
                todo.append(part)
    return parts


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _passed(detail: str) -> GateCheck:
    return GateCheck(name="image_evidence", passed=True, detail=detail)


def _no_evidence(ref: str) -> GateCheck:
    return GateCheck(name="image_evidence", passed=False,
                     detail=f"{ref} is no evidence of this block or its place: look at it first (view_source)")
