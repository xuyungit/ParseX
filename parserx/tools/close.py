"""``close``: the agent closes a worklist signal it checked against the page image (2026-09-25).

A signal points; the agent looks.  When the image shows nothing to change — the local reading took a logo for
text, a title candidate is a cover line, a table's arithmetic is the source's own — the agent closes the item
with its reason, so the document's open review counts only what is still open.  The program still decides: the
image must have been read in this workspace for the item's block or page (as for ``correct``), and only signals
close.  Failures (a pending page, a failed block, a skipped budget, a missing asset) are resolved by processing,
not closed.  A closed item stays closed while it reads the same; new content at the place opens it again.
"""

from __future__ import annotations

from parserx.content.select import GateCheck
from parserx.ir.base import IRModel
from parserx.ir.state import ClosedItem
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.correct import _image_evidence, _image_evidence_at
from parserx.tools.envelope import FailureCode, ToolFailure, UnresolvedKind
from parserx.tools.views import unresolved_items

CLOSABLE = frozenset({
    UnresolvedKind.TEXT_UNACCOUNTED, UnresolvedKind.TEXT_NOT_SEEN, UnresolvedKind.TITLE_CANDIDATE,
    UnresolvedKind.TEXT_SUSPICIOUS, UnresolvedKind.TABLE_ARITHMETIC, UnresolvedKind.TABLE_MERGE_CANDIDATE,
    UnresolvedKind.STRUCTURE_PENDING, UnresolvedKind.EVIDENCE_CONFLICT, UnresolvedKind.TABLE_UNCERTAIN,
})


class CloseRequest(IRModel):
    target: str  # the item's target: a block id or a page ("p3")
    kind: UnresolvedKind
    image: str  # the image it was checked on: an asset id ``read --image`` or ``ask_image`` returned
    reason: str
    actor: str = "agent"


class CloseResult(IRModel):
    closed: bool
    gate: list[GateCheck]


def run(ctx: ToolContext, req: CloseRequest) -> ToolOutput[CloseResult]:
    if req.kind not in CLOSABLE:
        raise ToolFailure(FailureCode.INVALID_REQUEST, f"{req.kind.value} is resolved by processing, not closed",
                          targets=[req.target])
    state = ctx.ws.load()
    item = next((u for u in unresolved_items(state) if u.target == req.target and u.kind == req.kind), None)
    if item is None:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no open {req.kind.value} item for {req.target}",
                          targets=[req.target])
    if req.target.startswith("p") and req.target[1:].isdigit():
        n = int(req.target[1:])
        page = next((p for p in state.pages if p.n == n), None)
        box = (0.0, 0.0, *page.size_pt) if page is not None and page.size_pt else (0.0, 0.0, 1e6, 1e6)
        image = _image_evidence_at(ctx, state, n, box, req.image)
    else:
        block = next((b for b in state.blocks if b.id == req.target), None)
        if block is None:
            raise ToolFailure(FailureCode.NOT_FOUND, f"no block {req.target}", targets=[req.target])
        image = _image_evidence(ctx, state, block, req.image)
    if not image.passed:
        return output(CloseResult(closed=False, gate=[image]))
    with ctx.ws.txn(f"tool:close:{req.actor}") as state:
        state.closed.append(ClosedItem(target=req.target, kind=req.kind.value, quotes=[q.doc_text for q in item.quotes],
                                       reason=req.reason, actor=req.actor, image=req.image))
    return output(CloseResult(closed=True, gate=[image]))
