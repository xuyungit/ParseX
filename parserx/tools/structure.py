"""``apply_structure``: roles, levels, order, relations and table continuation; never the text (guide §5.1, §6.8, §6.9)."""

from __future__ import annotations

from parserx.hierarchy import Rejection, StructureChange, apply_changes, check_changes
from parserx.ir.base import IRModel
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Change
from parserx.tools.views import OutlineNode, outline_nodes, unresolved_items


class ApplyStructureRequest(IRModel):
    changes: list[StructureChange]
    atomic: bool = False  # any rejection → nothing applied
    actor: str = "agent"  # who decided: agent / pipeline / adapter:v1 …, recorded on every Decision


class ApplyStructureResult(IRModel):
    accepted: list[int]
    rejected: list[Rejection]
    outline_after: list[OutlineNode]


_FIELDS = ("kind", "level", "order", "status", "rows")  # rows: a table's row count (merge_tables)


def run(ctx: ToolContext, req: ApplyStructureRequest) -> ToolOutput[ApplyStructureResult]:
    state = ctx.ws.load()
    rejected_up_front = check_changes(state, req.changes)
    if len(rejected_up_front) == len(req.changes) or (req.atomic and rejected_up_front):
        return output(ApplyStructureResult(accepted=[], rejected=rejected_up_front, outline_after=outline_nodes(state)))
    open_before = {(u.target, u.kind) for u in unresolved_items(state)}
    with ctx.ws.txn(f"tool:apply_structure:{req.actor}") as state:
        before = {b.id: {f: _value(b, f) for f in _FIELDS} for b in state.blocks}
        outcome = apply_changes(state, req.changes, actor=req.actor, atomic=req.atomic)
        diff = []
        for block in state.blocks:
            for f in _FIELDS:
                old = before[block.id][f] if block.id in before else None  # a block the call created (split)
                new = _value(block, f)
                if old != new:
                    diff.append(Change(target=block.id, field=f, before=_json(old), after=_json(new)))
        outline = outline_nodes(state)
    # what these changes opened (e.g. an image whose transcribed text was excluded now carries nothing)
    opened = [u for u in unresolved_items(ctx.ws.load()) if (u.target, u.kind) not in open_before]
    return output(ApplyStructureResult(accepted=outcome.accepted, rejected=outcome.rejected, outline_after=outline),
                  diff=diff, unresolved=opened)


def _value(block, field: str):
    if field == "rows":
        return block.cells.n_rows if block.cells is not None else None
    return getattr(block, field)


def _json(value):
    return value.value if hasattr(value, "value") else value
