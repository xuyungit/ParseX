"""Accounting check (guide §3.3, docs/v2_phase1_interfaces.md §5.8).

Every discovered item (native line, OCR block, OOXML node, image) is one
ledger entry with exactly one disposition.  The check verifies:

- balance: ``discovered = output + merged + duplicate + excluded + failed``,
  with no unassigned entry;
- agreement: an entry's disposition matches its block's status — an item
  marked ``output`` on a block that is not rendered is a silent loss;
- references: every id a block, relation, decision, anchor, page or image
  record names exists, and ids are unique;
- assets: every asset file exists (when the workspace root is given).

A document with pending pages, unassigned items, disagreements, illegal
references or missing asset files cannot be exported.  Known losses —
failed recognition, budget skips, unsupported elements — are accounted and
listed in ``missing``: the document exports as ``partial``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from parserx.ir.anchor import AssetAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockStatus, DocumentStatus, PageStatus
from parserx.ir.state import AccountingSummary, DocumentState, Missing
from parserx.workspace.views import PageRow, page_rows

_VISIBLE = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})
# The block statuses each disposition may sit on.
_AGREES = {
    "output": _VISIBLE,
    "merged": _VISIBLE | {BlockStatus.MERGED},  # merged into this visible block, or a merged container
    "duplicate": frozenset({BlockStatus.DUPLICATE}),
    "excluded": frozenset({BlockStatus.EXCLUDED}),
    "failed": frozenset({BlockStatus.FAILED}) | _VISIBLE,  # a failed recognition may keep a visible fallback
}
_FAILED_PAGES = frozenset({PageStatus.FAILED, PageStatus.SKIPPED})


class IllegalRef(IRModel):
    source: str  # the object holding the reference
    field: str
    target: str


class CheckResult(IRModel):
    accounting: AccountingSummary
    unassigned: list[str]
    mismatched: list[str]  # ledger items whose disposition disagrees with their block's status
    illegal_refs: list[IllegalRef]
    missing_assets: list[str]
    pages: list[PageRow]
    document_status: DocumentStatus
    missing: list[Missing]
    exportable: bool


def check(state: DocumentState, root: Path | str | None = None) -> CheckResult:
    counts = Counter(entry.disposition for entry in state.ledger)
    summary = AccountingSummary(
        discovered=len(state.ledger), output=counts["output"], merged=counts["merged"],
        duplicate=counts["duplicate"], excluded=counts["excluded"], failed=counts["failed"],
        unassigned=counts[None],
    )
    blocks = {b.id: b for b in state.blocks}
    unassigned = [e.item for e in state.ledger if e.disposition is None]
    mismatched = [e.item for e in state.ledger if e.disposition is not None and e.block in blocks
                  and blocks[e.block].status not in _AGREES[e.disposition]]
    illegal = _illegal_refs(state)
    missing_assets = []
    if root is not None:
        missing_assets = [a.id for a in state.assets if not (Path(root) / a.path).is_file()]
    missing = _missing(state)
    status = _document_status(state, missing)
    pending = any(p.status == PageStatus.PENDING for p in state.pages)
    return CheckResult(
        accounting=summary, unassigned=unassigned, mismatched=mismatched, illegal_refs=illegal,
        missing_assets=missing_assets, pages=page_rows(state), document_status=status, missing=missing,
        exportable=not (unassigned or mismatched or illegal or missing_assets or pending),
    )


def _illegal_refs(state: DocumentState) -> list[IllegalRef]:
    out: list[IllegalRef] = []
    for kind, values in (("block", [b.id for b in state.blocks]), ("ledger", [e.item for e in state.ledger]),
                         ("relation", [r.id for r in state.relations]), ("asset", [a.id for a in state.assets])):
        for value, n in sorted(Counter(values).items()):
            if n > 1:
                out.append(IllegalRef(source=kind, field="id", target=value))
    blocks = {b.id for b in state.blocks}
    observations = {o.id for b in state.blocks for o in b.observations}
    relations = {r.id for r in state.relations}
    assets = {a.id for a in state.assets}
    known = blocks | observations | relations | assets
    for entry in state.ledger:
        if entry.block is not None and entry.block not in blocks:
            out.append(IllegalRef(source=entry.item, field="block", target=entry.block))
    for relation in state.relations:
        for field in ("src", "dst"):
            if getattr(relation, field) not in blocks:
                out.append(IllegalRef(source=relation.id, field=field, target=getattr(relation, field)))
    for block in state.blocks:
        for anchor in block.anchors:
            if isinstance(anchor, AssetAnchor) and anchor.asset not in assets:
                out.append(IllegalRef(source=block.id, field="anchors.asset", target=anchor.asset))
        for decision in block.decisions:
            out.extend(IllegalRef(source=block.id, field="decisions.refs", target=ref)
                       for ref in decision.refs if ref not in known)
    for asset in state.assets:
        if asset.derived_from is not None and asset.derived_from not in assets:
            out.append(IllegalRef(source=asset.id, field="derived_from", target=asset.derived_from))
    for page in state.pages:
        if page.render is not None and page.render not in assets:
            out.append(IllegalRef(source=f"page {page.n}", field="render", target=page.render))
    for record in state.images:
        if record.id not in assets:
            out.append(IllegalRef(source="images", field="id", target=record.id))
    for item in state.missing:
        if item.block not in blocks:
            out.append(IllegalRef(source="missing", field="block", target=item.block))
    return out


def _missing(state: DocumentState) -> list[Missing]:
    """Recorded losses plus failed blocks nobody recorded yet (reason: their last Decision)."""
    listed = {m.block for m in state.missing}
    extra = [Missing(block=b.id, reason=b.decisions[-1].reason if b.decisions else "failed")
             for b in state.blocks if b.status == BlockStatus.FAILED and b.id not in listed]
    return sorted([*state.missing, *extra], key=lambda m: m.block)


def _document_status(state: DocumentState, missing: list[Missing]) -> DocumentStatus:
    if any(p.status == PageStatus.PENDING for p in state.pages):
        return DocumentStatus.IN_PROGRESS
    if not any(e.disposition in ("output", "merged") for e in state.ledger):
        return DocumentStatus.FAILED  # nothing extracted reaches the output (guide §4.5: extraction failed)
    partial = (missing or any(p.status in _FAILED_PAGES | {PageStatus.PARTIAL} for p in state.pages)
               or any(e.disposition == "failed" for e in state.ledger))
    return DocumentStatus.PARTIAL if partial else DocumentStatus.COMPLETE
