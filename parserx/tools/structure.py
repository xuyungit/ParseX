"""``apply_structure``: roles, levels, order and relations; never the text (guide §5.1, §6.8)."""

from __future__ import annotations

from parserx.hierarchy import Rejection, StructureChange, apply_changes, check_changes
from parserx.ir.base import IRModel
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Change
from parserx.tools.views import OutlineNode, outline_nodes


class ApplyStructureRequest(IRModel):
    changes: list[StructureChange]
    atomic: bool = False  # any rejection → nothing applied
    actor: str = "agent"  # who decided: agent / pipeline / adapter:v1 …, recorded on every Decision


class ApplyStructureResult(IRModel):
    accepted: list[int]
    rejected: list[Rejection]
    outline_after: list[OutlineNode]


_FIELDS = ("kind", "level", "order", "status")


def run(ctx: ToolContext, req: ApplyStructureRequest) -> ToolOutput[ApplyStructureResult]:
    state = ctx.ws.load()
    rejected_up_front = check_changes(state, req.changes)
    if len(rejected_up_front) == len(req.changes) or (req.atomic and rejected_up_front):
        return output(ApplyStructureResult(accepted=[], rejected=rejected_up_front, outline_after=outline_nodes(state)))
    with ctx.ws.txn(f"tool:apply_structure:{req.actor}") as state:
        before = {b.id: {f: getattr(b, f) for f in _FIELDS} for b in state.blocks}
        outcome = apply_changes(state, req.changes, actor=req.actor, atomic=req.atomic)
        diff = []
        for block in state.blocks:
            for f in _FIELDS:
                old, new = before[block.id][f], getattr(block, f)
                if old != new:
                    diff.append(Change(target=block.id, field=f, before=_json(old), after=_json(new)))
        outline = outline_nodes(state)
    return output(ApplyStructureResult(accepted=outcome.accepted, rejected=outcome.rejected, outline_after=outline),
                  diff=diff)


def _json(value):
    return value.value if hasattr(value, "value") else value
