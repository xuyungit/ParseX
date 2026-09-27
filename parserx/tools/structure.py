"""``apply_structure``: roles, levels, order, relations and table continuation; never the text (guide §5.1, §6.8, §6.9)."""

from __future__ import annotations

from parserx.hierarchy import Rejection, StructureChange, apply_changes, check_changes
from parserx.ir.base import IRModel
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.views import OutlineNode, outline_nodes


class ApplyStructureRequest(IRModel):
    changes: list[StructureChange]
    atomic: bool = False  # any rejection → nothing applied
    actor: str = "agent"  # who decided: agent / pipeline / program:<module> …, recorded on every Decision


class ApplyStructureResult(IRModel):
    accepted: list[int]
    rejected: list[Rejection]
    outline_after: list[OutlineNode]


def run(ctx: ToolContext, req: ApplyStructureRequest) -> ToolOutput[ApplyStructureResult]:
    state = ctx.ws.load()
    rejected_up_front = check_changes(state, req.changes)
    if len(rejected_up_front) == len(req.changes) or (req.atomic and rejected_up_front):
        return output(ApplyStructureResult(accepted=[], rejected=rejected_up_front, outline_after=outline_nodes(state)))
    with ctx.ws.txn(f"tool:apply_structure:{req.actor}") as state:
        outcome = apply_changes(state, req.changes, actor=req.actor, atomic=req.atomic)
        outline = outline_nodes(state)
    return output(ApplyStructureResult(accepted=outcome.accepted, rejected=outcome.rejected, outline_after=outline))
