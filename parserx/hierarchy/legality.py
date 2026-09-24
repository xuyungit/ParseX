"""Legality of structure changes (guide §6.8, §3.3 "合法性").

Checks are correctness constraints, not guesses about meaning:

- references point to existing blocks and relations;
- only structural kinds change role (content kinds keep their form);
- ``level`` only on titles; the outline never skips a level
  (a title is at most one level deeper than the title before it, and its
  successor at most one level deeper than it);
- titles sharing a numbering pattern (``1.2`` / ``1.3`` → ``N.N``,
  ``第二章`` → ``第N章``) share a level within the document;
- reordering cannot form a cycle; relations are not duplicated.

Changes are checked one by one against the state as the earlier accepted
changes of the batch leave it, so a batch can set a role and then a level.
"""

from __future__ import annotations

import re

from parserx.hierarchy.changes import (
    AddRelation,
    ApplyOutcome,
    LegalityRule,
    MarkPending,
    MoveAfter,
    Rejection,
    RemoveRelation,
    SetLevel,
    SetRole,
    StructureChange,
)
from parserx.ir import ids
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, ordered

_CONTENT_KINDS = frozenset({BlockKind.TABLE, BlockKind.FIGURE, BlockKind.FORMULA, BlockKind.SCAN})
_NUMERAL = r"[0-9０-９]+|[一二三四五六七八九十百千零〇两]+|[IVXLCDM]+|[ivxlcdm]+"
_NUMBERING_RE = re.compile(
    r"^\s*(?:"
    r"第\s*(?:[0-9０-９]+|[一二三四五六七八九十百千零〇两]+)\s*[章节条篇部编]"  # 第三章
    r"|[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)+\.?"  # 1.2 / 1.2.3.
    r"|[(（]\s*(?:" + _NUMERAL + r")\s*[)）]"  # （一） (3)
    r"|(?:" + _NUMERAL + r")\s*[、.．)）]"  # 一、 3. 3)
    r"|[A-Za-z]\s*[.)]"  # A. a)
    r")"
)
_NUMERAL_RE = re.compile(_NUMERAL)


def numbering_signature(text: str) -> str | None:
    """The shape of a leading number (``1.2.3`` → ``N.N.N``), or None when the text starts without one."""
    match = _NUMBERING_RE.match(text)
    if not match:
        return None
    token = re.sub(r"\s+", "", match.group(0)).rstrip(".．")
    token = re.sub(r"^[A-Za-z](?=[.)])", "L", token)
    return _NUMERAL_RE.sub("N", token).replace("．", ".")


def check_changes(state: DocumentState, changes: list[StructureChange]) -> list[Rejection]:
    """Rejections for *changes* applied in order to a copy of *state* (the state itself is not modified)."""
    return apply_changes(state.model_copy(deep=True), changes, actor="check", atomic=False).rejected


def apply_changes(state: DocumentState, changes: list[StructureChange], *, actor: str,
                  atomic: bool = False) -> ApplyOutcome:
    """Apply the legal changes in order and record a Decision for each; with *atomic*, all or nothing."""
    if atomic:
        rejected = check_changes(state, changes)
        if rejected:
            return ApplyOutcome(accepted=[], rejected=rejected)
    outcome = ApplyOutcome()
    for index, change in enumerate(changes):
        problem = _problem(state, change)
        if problem is not None:
            outcome.rejected.append(Rejection(index=index, rule=problem[0], detail=problem[1]))
            continue
        _apply(state, change, actor)
        outcome.accepted.append(index)
    return outcome


# ── Checks ──────────────────────────────────────────────────────────────


def _problem(state: DocumentState, change: StructureChange) -> tuple[LegalityRule, str] | None:
    blocks = {b.id: b for b in state.blocks}
    for name in _block_refs(change):
        if name not in blocks:
            return LegalityRule.UNKNOWN_BLOCK, f"no block {name}"
    if isinstance(change, RemoveRelation) and all(r.id != change.relation for r in state.relations):
        return LegalityRule.UNKNOWN_BLOCK, f"no relation {change.relation}"
    if isinstance(change, SetRole):
        if blocks[change.block].kind in _CONTENT_KINDS:
            return LegalityRule.KIND_NOT_STRUCTURAL, f"{change.block} is a {blocks[change.block].kind}"
        return None
    if isinstance(change, SetLevel):
        block = blocks[change.block]
        if block.kind != BlockKind.TITLE:
            return LegalityRule.LEVEL_ON_NON_TITLE, f"{change.block} is a {block.kind}, not a title"
        if change.level is not None:
            return _level_problem(state, block, change.level)
        return None
    if isinstance(change, MoveAfter):
        if change.after == change.block or _moves_into_itself(state, change):
            return LegalityRule.ORDER_CYCLE, f"{change.block} cannot follow {change.after}"
        return None
    if isinstance(change, AddRelation):
        if any((r.kind, r.src, r.dst) == (change.kind, change.src, change.dst) for r in state.relations):
            return LegalityRule.DUPLICATE_RELATION, f"{change.kind} {change.src} → {change.dst} exists"
    return None


def _block_refs(change: StructureChange) -> list[str]:
    if isinstance(change, (SetRole, SetLevel, MarkPending)):
        return [change.block]
    if isinstance(change, MoveAfter):
        return [change.block] + ([change.after] if change.after else [])
    if isinstance(change, AddRelation):
        return [change.src, change.dst]
    return []


def _level_problem(state: DocumentState, block: Block, level: int) -> tuple[LegalityRule, str] | None:
    titles = [b for b in ordered(state) if b.kind == BlockKind.TITLE and b.status not in HIDDEN]
    position = next(i for i, b in enumerate(titles) if b.id == block.id)
    before = next((b.level for b in reversed(titles[:position]) if b.level is not None), None)
    after = next((b.level for b in titles[position + 1:] if b.level is not None), None)
    if before is not None and level > before + 1:
        return LegalityRule.LEVEL_SKIP, f"H{before} → H{level}"
    if after is not None and after > level + 1:
        return LegalityRule.LEVEL_SKIP, f"H{level} → H{after}"
    signature = numbering_signature(block.text)
    if signature is not None:
        for other in titles:
            if other.id != block.id and other.level is not None and other.level != level \
                    and numbering_signature(other.text) == signature:
                return (LegalityRule.NUMBERING_LEVEL_INCONSISTENT,
                        f"pattern {signature!r} is H{other.level} at {other.id}")
    return None


def _moves_into_itself(state: DocumentState, change: MoveAfter) -> bool:
    """Following *after* would require *after* to follow the block already (a chain back to the block)."""
    seen: set[str] = set()
    target = change.after
    moved_after = {d.refs[0]: d.refs[1] for b in state.blocks for d in b.decisions
                   if d.stage == DecisionStage.STRUCTURE and d.choice == "move_after" and len(d.refs) == 2}
    while target is not None and target not in seen:
        if target == change.block:
            return True
        seen.add(target)
        target = moved_after.get(target)
    return False


# ── Application ─────────────────────────────────────────────────────────


def _apply(state: DocumentState, change: StructureChange, actor: str) -> None:
    blocks = {b.id: b for b in state.blocks}
    if isinstance(change, SetRole):
        block = blocks[change.block]
        block.kind = BlockKind(change.kind)
        if block.kind != BlockKind.TITLE:
            block.level = None
        block.decisions.append(Decision(stage=DecisionStage.HEADING_ROLE, choice=change.kind, reason=change.reason,
                                        evidence=change.evidence, actor=actor))
    elif isinstance(change, SetLevel):
        block = blocks[change.block]
        block.level = change.level
        block.decisions.append(Decision(stage=DecisionStage.HEADING_LEVEL, choice=str(change.level),
                                        reason=change.reason, evidence=change.evidence, actor=actor))
    elif isinstance(change, MoveAfter):
        block = blocks[change.block]
        sequence = [b for b in ordered(state) if b.id != block.id]
        at = 0 if change.after is None else next(i for i, b in enumerate(sequence) if b.id == change.after) + 1
        sequence.insert(at, block)
        for order, item in enumerate(sequence):
            item.order = order
        block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="move_after", reason=change.reason,
                                        evidence={}, actor=actor, refs=[block.id] + ([change.after] if change.after
                                                                                     else [])))
    elif isinstance(change, AddRelation):
        state.relations.append(Relation(id=ids.relation_id(change.kind, change.src, change.dst), kind=change.kind,
                                        src=change.src, dst=change.dst, confidence=change.confidence))
    elif isinstance(change, RemoveRelation):
        state.relations[:] = [r for r in state.relations if r.id != change.relation]
    elif isinstance(change, MarkPending):
        block = blocks[change.block]
        block.status = BlockStatus.DEGRADED
        block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="pending", reason=change.reason,
                                        evidence={}, actor=actor))
