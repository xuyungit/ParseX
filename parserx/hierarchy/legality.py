"""Legality of structure changes (guide §6.8, §3.3 "合法性").

Checks are correctness constraints, not guesses about meaning:

- references point to existing blocks and relations;
- only structural kinds change role (content kinds keep their form);
- the outline never skips a level (a title is at most one level deeper than
  the title before it, and its successor at most one level deeper than it);
- titles sharing a numbering pattern (``1.2`` / ``1.3`` → ``N.N``,
  ``第二章`` → ``第N章``, ``1 Scope`` / ``2. Terms`` → ``N``) share a level
  within the document — an experience rule: a change with ``override`` and
  evidence may break it (Q87);
- reordering cannot form a cycle;
- ``join`` joins two paragraphs or two tables in the output; tables only with
  the same columns (a correctness constraint), on the next page with only page
  furniture between (an experience rule, ``override`` with evidence), and only
  rows that repeat the first table's header are dropped;
- the program's proposals leave alone the blocks whose structure the agent
  decided.

Changes are checked one by one against the state as the earlier accepted
changes of the batch leave it; ``apply_batch`` judges the outline as a run of
changes leaves it.
"""

from __future__ import annotations

import re

from parserx.hierarchy.changes import (
    ApplyOutcome,
    Exclude,
    Include,
    Join,
    LegalityRule,
    MarkPending,
    Move,
    Rejection,
    SetRole,
    Split,
    StructureChange,
    Unjoin,
)
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.observation import Observation
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, share_containers
from parserx.layout.labels import FURNITURE
from parserx.tables.merge import merge_candidate, merge_tables, repeats_header, split_problem, split_tables
from parserx.workspace.queries import HIDDEN, JOINABLE, ordered

_CONTENT_KINDS = frozenset({BlockKind.TABLE, BlockKind.FIGURE, BlockKind.FORMULA, BlockKind.SCAN})
_NUMERAL = r"[0-9０-９]+|[一二三四五六七八九十百千零〇两]+|[IVXLCDM]+|[ivxlcdm]+"
NUMBERING_RE = re.compile(
    r"^\s*(?:"
    r"第\s*(?:[0-9０-９]+|[一二三四五六七八九十百千零〇两]+)\s*[章节条篇部编]"  # 第三章
    r"|[A-Za-z]\s*[.．]\s*[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)*"  # C.1 / C.0.1 (appendix), before Roman C.
    r"|[0-9０-９]+(?:\s*[.．]\s*[0-9０-９]+)+\.?"  # 1.2 / 1.2.3.
    r"|[(（]\s*(?:" + _NUMERAL + r")\s*[)）]"  # （一） (3)
    r"|(?:" + _NUMERAL + r")\s*[、.．)）]"  # 一、 3. 3)
    r"|[A-Za-z]\s*[.)]"  # A. a)
    r"|[0-9０-９]{1,3}(?=\s+\S)"  # 1 Introduction (not 2024 年: years are not section numbers)
    r")"
)
_NUMERAL_RE = re.compile(_NUMERAL)


def numbering_signature(text: str) -> str | None:
    """The shape of a leading number, or None when the text starts without one: ``1.2.3`` → ``N.N.N``.  The numerals'
    class is part of the style (P4-3): arabic ``N``, Chinese ``C``, Roman ``R`` / ``r`` — ``一、`` and ``1、``,
    ``（一）`` and ``（1）`` are different levels of one document.  In ``第…章`` the unit makes the style: ``第N章``."""
    match = NUMBERING_RE.match(text)
    if not match:
        return None
    token = re.sub(r"\s+", "", match.group(0)).rstrip(".．")
    if token.startswith("第"):
        return _NUMERAL_RE.sub("N", token)
    # A leading letter before a delimiter is a letter sequence (A., b), C.1), except I / V / X: Roman numerals.
    letter = re.match(r"(?![IVXivx](?:[.．)]|$))[A-Za-z](?=[.．)]|$)", token)
    if letter:
        return "L" + _NUMERAL_RE.sub(_numeral_class, token[1:]).replace("．", ".")
    return _NUMERAL_RE.sub(_numeral_class, token).replace("．", ".")


def _numeral_class(match: re.Match) -> str:
    numeral = match.group(0)
    if numeral[0] in "0123456789０１２３４５６７８９":
        return "N"
    if numeral[0].isascii():
        return "R" if numeral[0].isupper() else "r"
    return "C"


def check_changes(state: DocumentState, changes: list[StructureChange]) -> list[Rejection]:
    """Rejections for *changes* applied in order to a copy of *state* (the state itself is not modified)."""
    return apply_changes(state.model_copy(deep=True), changes, actor="check", atomic=False).rejected


def apply_changes(state: DocumentState, changes: list[StructureChange], *, actor: str,
                  atomic: bool = False, levels: bool = True) -> ApplyOutcome:
    """Apply the legal changes in order and record a Decision for each; with *atomic*, all or nothing.  *levels*
    False leaves the outline rules (no skipped level, one level per numbering pattern) to the caller."""
    if atomic:
        rejected = check_changes(state, changes)
        if rejected:
            return ApplyOutcome(accepted=[], rejected=rejected)
    outcome = ApplyOutcome()
    for index, change in enumerate(changes):
        problem = _problem(state, change, levels=levels) or _decided_by_agent(state, change, actor)
        if problem is not None:
            outcome.rejected.append(Rejection(index=index, rule=problem[0], detail=problem[1]))
            continue
        _apply(state, change, actor)
        outcome.accepted.append(index)
    return outcome


def apply_batch(state: DocumentState, changes: list[StructureChange], *, actor: str) -> ApplyOutcome:
    """Apply *changes* judged by the outline they produce, not step by step: an outline is re-levelled as a whole
    (every title of a numbering pattern moves at once, though each move alone would break the pattern's level).
    The other rules are checked change by change as usual.  A change whose title is illegal in the result is
    refused with the reason, and the rest is judged again, until what remains is legal."""
    remaining = list(range(len(changes)))
    refused: dict[int, Rejection] = {}
    while True:
        trial = state.model_copy(deep=True)
        outcome = apply_changes(trial, [changes[i] for i in remaining], actor=actor, levels=False)
        for rejection in outcome.rejected:
            index = remaining[rejection.index]
            refused[index] = Rejection(index=index, rule=rejection.rule, detail=rejection.detail)
        applied = [remaining[j] for j in outcome.accepted]
        blocks = {b.id: b for b in trial.blocks}
        illegal, judged = {}, set()
        for index in reversed(applied):  # a block's level is the last change's: that one answers for it
            change = changes[index]
            if isinstance(change, SetRole) and change.level is not None and change.block not in judged:
                judged.add(change.block)
                block = blocks[change.block]
                if block.kind == BlockKind.TITLE and block.level is not None:
                    problem = _level_problem(trial, block, block.level, numbering=not change.override)
                    if problem is not None:
                        illegal[index] = Rejection(index=index, rule=problem[0], detail=problem[1])
        if not illegal:
            break
        refused.update(illegal)
        remaining = [i for i in applied if i not in illegal]
    final = apply_changes(state, [changes[i] for i in applied], actor=actor, levels=False)
    assert not final.rejected, final.rejected  # the trial ran the same changes on the same state
    return ApplyOutcome(accepted=applied, rejected=sorted(refused.values(), key=lambda r: r.index))


# ── Checks ──────────────────────────────────────────────────────────────


def _problem(state: DocumentState, change: StructureChange, *, levels: bool = True) -> tuple[LegalityRule, str] | None:
    blocks = {b.id: b for b in state.blocks}
    for name in _block_refs(change):
        if name not in blocks:
            return LegalityRule.UNKNOWN_BLOCK, f"no block {name}"
    if getattr(change, "override", False) and not _evidence_exists(state, change.evidence):
        return (LegalityRule.OVERRIDE_WITHOUT_EVIDENCE,
                "an exception to a document convention rests on evidence: give the evidence id (view_source) and why")
    if isinstance(change, (SetRole, Include)) and blocks[change.block].status == BlockStatus.EXCLUDED \
            and blocks[change.block].kind in FURNITURE and not _evidence_exists(state, change.evidence):
        return (LegalityRule.FURNITURE_WITHOUT_EVIDENCE,
                f"{change.block} is page furniture: the export keeps it in its page's marker line (页眉：…), nothing "
                "is lost; bring it into the body only if the page shows it is body text — give the evidence id "
                "(view_source) and why")
    if isinstance(change, SetRole):
        block = blocks[change.block]
        if block.kind in _CONTENT_KINDS:
            return LegalityRule.KIND_NOT_STRUCTURAL, f"{change.block} is a {block.kind}"
        if change.level is not None and levels:
            kind, status = block.kind, block.status
            block.kind = BlockKind.TITLE  # judge the level as the title it is about to become
            if kind in FURNITURE:
                block.status = BlockStatus.OK  # page furniture given a body role comes back
            try:
                return _level_problem(state, block, change.level, numbering=not change.override)
            finally:
                block.kind, block.status = kind, status
        return None
    if isinstance(change, Exclude):
        if not change.reason.strip():
            return LegalityRule.REASON_REQUIRED, "content leaves the output only with a reason"
        if blocks[change.block].status in HIDDEN:
            return LegalityRule.NOT_VISIBLE, f"{change.block} is {blocks[change.block].status}"
        return None
    if isinstance(change, Include):
        block = blocks[change.block]
        if block.status != BlockStatus.EXCLUDED:
            return LegalityRule.NOT_EXCLUDED, f"{change.block} is {block.status}"
        excluded = [d for d in block.decisions if d.stage == DecisionStage.EXCLUDE]
        if excluded and excluded[-1].choice == "revision_deleted":
            return LegalityRule.NOT_RESTORABLE, "text deleted by a revision stays deleted (Q26)"
        return None
    if isinstance(change, Move):
        if change.after == change.block or _moves_into_itself(state, change):
            return LegalityRule.ORDER_CYCLE, f"{change.block} cannot follow {change.after}"
        return None
    if isinstance(change, Join):
        return _join_problem(state, blocks[change.first], blocks[change.second], change.drop_rows, change.override)
    if isinstance(change, Unjoin):
        if _joined(state, change.first, change.second) is None:
            return LegalityRule.NOT_JOINED, f"{change.second} does not continue {change.first}"
        if blocks[change.second].status == BlockStatus.MERGED:
            problem = split_problem(state, blocks[change.first], blocks[change.second])
            return None if problem is None else (LegalityRule.TABLES_MERGED, problem)
        return None
    if isinstance(change, Split):
        block = blocks[change.block]
        if block.kind in _CONTENT_KINDS or block.cells is not None:
            return LegalityRule.KIND_NOT_STRUCTURAL, f"{change.block} is a {block.kind}"
        if block.status in HIDDEN:
            return LegalityRule.NOT_VISIBLE, f"{change.block} is {block.status}"
        if _split_parts(block.text, change.at_break) is None:
            return LegalityRule.NO_LINE_BREAK, f"{change.block} has no line break {change.at_break} with text on both sides"
        return None
    return None


def _join_problem(state: DocumentState, first: Block, second: Block, drop_rows: int,
                  override: bool = False) -> tuple[LegalityRule, str] | None:
    if first.id == second.id or first.status in HIDDEN or second.status in HIDDEN:
        return LegalityRule.NOT_JOINABLE, f"{second.id} and {first.id}: two different blocks, both in the output"
    if first.kind == BlockKind.TABLE and second.kind == BlockKind.TABLE:
        if first.cells is None or second.cells is None or first.cells.n_cols != second.cells.n_cols:
            return (LegalityRule.NOT_MERGE_CANDIDATE, f"{second.id} cannot continue {first.id}: one table has one "
                    "set of columns")
        if not override and merge_candidate(state, first, second) is None:
            return (LegalityRule.NOT_ADJACENT, f"{second.id} is not on the page after {first.id} with only page "
                    "furniture between; if the source shows it continues all the same, override with evidence")
        if drop_rows and not repeats_header(first.cells, second.cells, drop_rows):
            return (LegalityRule.ROWS_NOT_DUPLICATE,
                    f"the first {drop_rows} rows of {second.id} do not repeat the header of {first.id}")
        return None
    if first.kind not in JOINABLE or second.kind not in JOINABLE:
        return (LegalityRule.NOT_JOINABLE, f"{first.id} is {_role(first)}, {second.id} is {_role(second)}: join joins "
                "two paragraphs (text, list, footnote, other) or two tables")
    if drop_rows:
        return LegalityRule.NOT_JOINABLE, "drop_rows is for tables"
    if _joined(state, first.id, second.id) is not None:
        return LegalityRule.ALREADY_JOINED, f"{second.id} continues {first.id} already"
    return None


def _role(block: Block) -> str:
    return f"H{block.level}" if block.kind == BlockKind.TITLE and block.level else block.kind.value


_STRUCTURE_STAGES = frozenset({DecisionStage.HEADING_ROLE, DecisionStage.HEADING_LEVEL, DecisionStage.STRUCTURE,
                               DecisionStage.EXCLUDE})


def _program(actor: str) -> bool:
    return actor.startswith(("program:", "pipeline"))


def _decided_by_agent(state: DocumentState, change: StructureChange, actor: str) -> tuple[LegalityRule, str] | None:
    if not _program(actor):
        return None
    blocks = {b.id: b for b in state.blocks}
    for name in _block_refs(change):
        decided = [d for d in blocks[name].decisions if d.stage in _STRUCTURE_STAGES and not _program(d.actor)]
        if decided:
            return LegalityRule.DECIDED_BY_AGENT, f"{decided[-1].actor} decided {name}: {decided[-1].choice}"
    return None


def _block_refs(change: StructureChange) -> list[str]:
    if isinstance(change, (SetRole, MarkPending, Exclude, Include, Split)):
        return [change.block]
    if isinstance(change, Move):
        return [change.block] + ([change.after] if change.after else [])
    if isinstance(change, (Join, Unjoin)):
        return [change.first, change.second]
    return []


def _evidence_exists(state: DocumentState, evidence) -> bool:
    return isinstance(evidence, str) and any(e.id == evidence for e in state.evidence)


def _level_problem(state: DocumentState, block: Block, level: int, *,
                   numbering: bool = True) -> tuple[LegalityRule, str] | None:
    titles = [b for b in ordered(state) if b.kind == BlockKind.TITLE and b.status not in HIDDEN]
    position = next(i for i, b in enumerate(titles) if b.id == block.id)
    before = next((b.level for b in reversed(titles[:position]) if b.level is not None), None)
    after = next((b.level for b in titles[position + 1:] if b.level is not None), None)
    if before is not None and level > before + 1:
        return LegalityRule.LEVEL_SKIP, f"H{before} → H{level}"
    if after is not None and after > level + 1:
        return LegalityRule.LEVEL_SKIP, f"H{level} → H{after}"
    signature = numbering_signature(block.text) if numbering else None
    if signature is not None:
        # a document inside an embedded image numbers on its own (Q42, Q43): compare within the same image only
        container = {r.dst: r.src for r in state.relations if r.kind == RelationKind.CONTAINS}
        scope = container.get(block.id)
        for index, other in enumerate(titles):
            if other.id != block.id and other.level is not None and other.level != level \
                    and container.get(other.id) == scope and numbering_signature(other.text) == signature \
                    and _same_section(titles, position, index, level, other.level, signature):
                return (LegalityRule.NUMBERING_LEVEL_INCONSISTENT,
                        f"pattern {signature!r} is H{other.level} at {other.id} (if the source shows this title is "
                        "an exception, override with evidence)")
    return None


def _family(signature: str) -> str:
    """Numbering systems: 1 / 1.1 / 1.1.1 are one, the appendix's C / C.1 another, every other shape its own."""
    if re.fullmatch(r"N(\.N)*", signature):
        return "decimal"
    if re.fullmatch(r"L(\.N)*", signature):
        return "letter"
    return signature


def _same_section(titles: list[Block], i: int, j: int, level_i: int, level_j: int, signature: str) -> bool:
    """Q43: two titles of one numbering pattern belong to the same section unless a title of another numbering
    system, no deeper than the shallower of the two, lies between them (an attachment restarting "1 …")."""
    family = _family(signature)
    bound = min(level_i, level_j)
    for other in titles[min(i, j) + 1:max(i, j)]:
        if other.level is None or other.level > bound:
            continue
        other_signature = numbering_signature(other.text)
        if other_signature is None or _family(other_signature) != family:
            return False
    return True


def _moves_into_itself(state: DocumentState, change: Move) -> bool:
    """Following *after* would require *after* to follow the block already (a chain back to the block)."""
    seen: set[str] = set()
    target = change.after
    moved_after = {d.refs[0]: d.refs[1] for b in state.blocks for d in b.decisions
                   if d.stage == DecisionStage.STRUCTURE and d.choice == "move" and len(d.refs) == 2}
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
        if block.kind in FURNITURE and block.status == BlockStatus.EXCLUDED:  # a body role: it is output again
            _set_excluded(state, block, False, change.reason, actor, _grounds(change))
        if change.level is None:
            block.level = None  # before the kind: a text block never holds a level, not even in between
        block.kind = BlockKind(change.kind)
        block.level = change.level
        block.decisions.append(Decision(stage=DecisionStage.HEADING_ROLE, choice=change.kind, reason=change.reason,
                                        evidence=_grounds(change), actor=actor))
        if change.level is not None:
            block.decisions.append(Decision(stage=DecisionStage.HEADING_LEVEL, choice=str(change.level),
                                            reason=change.reason, evidence=_grounds(change), actor=actor))
    elif isinstance(change, (Exclude, Include)):
        block = blocks[change.block]
        _set_excluded(state, block, isinstance(change, Exclude), change.reason, actor, _grounds(change))
        if isinstance(change, Include) and block.kind in FURNITURE:  # page furniture comes back as text
            block.kind = BlockKind.TEXT
            block.decisions.append(Decision(stage=DecisionStage.HEADING_ROLE, choice="text", reason=change.reason,
                                            evidence=_grounds(change), actor=actor))
    elif isinstance(change, Move):
        block = blocks[change.block]
        sequence = [b for b in ordered(state) if b.id != block.id]
        at = 0 if change.after is None else next(i for i, b in enumerate(sequence) if b.id == change.after) + 1
        sequence.insert(at, block)
        for order, item in enumerate(sequence):
            item.order = order
        block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="move", reason=change.reason,
                                        evidence=_grounds(change), actor=actor,
                                        refs=[block.id] + ([change.after] if change.after else [])))
    elif isinstance(change, Join):
        if blocks[change.first].kind == BlockKind.TABLE:
            merge_tables(state, change.first, change.second, change.drop_rows, actor=actor, reason=change.reason,
                         evidence=_grounds(change))
        else:
            state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTINUES, change.first, change.second),
                                            kind=RelationKind.CONTINUES, src=change.first, dst=change.second))
            blocks[change.second].decisions.append(Decision(
                stage=DecisionStage.STRUCTURE, choice="join", reason=change.reason, evidence=_grounds(change),
                actor=actor, refs=[change.first, change.second]))
    elif isinstance(change, Unjoin) and blocks[change.second].status == BlockStatus.MERGED:
        split_tables(state, change.first, change.second, actor=actor, reason=change.reason,
                     evidence=_grounds(change))
    elif isinstance(change, Unjoin):
        gone = _joined(state, change.first, change.second)
        state.relations[:] = [r for r in state.relations if r is not gone]
        blocks[change.second].decisions.append(Decision(
            stage=DecisionStage.STRUCTURE, choice="unjoin", reason=change.reason, evidence=_grounds(change),
            actor=actor, refs=[change.first, change.second]))
    elif isinstance(change, Split):
        _split(state, blocks[change.block], change.at_break, change.reason, actor)
    elif isinstance(change, MarkPending):
        block = blocks[change.block]
        block.status = BlockStatus.DEGRADED
        block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="pending", reason=change.reason,
                                        evidence=_grounds(change), actor=actor))


def _grounds(change) -> dict:
    """A change's grounds as a Decision records them: named facts, or the evidence id it cites (Q85), and whether it
    breaks a document convention on that evidence (Q87)."""
    evidence = change.evidence
    grounds = {"evidence": evidence} if isinstance(evidence, str) else dict(evidence)
    if getattr(change, "override", False):
        grounds["override"] = True
    return grounds


def _joined(state: DocumentState, first: str, second: str):
    """The relation by which *second* continues *first*, if any."""
    return next((r for r in state.relations
                 if (r.kind, r.src, r.dst) == (RelationKind.CONTINUES, first, second)), None)


def _split_parts(text: str | None, at_break: int) -> tuple[str, str] | None:
    """The text before and after its *at_break*-th line break, both with text; None when there is no such break."""
    parts = (text or "").split("\n")
    if at_break >= len(parts):
        return None
    first, second = "\n".join(parts[:at_break]).rstrip(), "\n".join(parts[at_break:]).lstrip()
    return (first, second) if first.strip() and second.strip() else None


def _split(state: DocumentState, block: Block, at_break: int, reason: str, actor: str) -> None:
    """The text after the break becomes a new text block right after *block*, on the same anchors; the ledger stays
    with *block* (the source item is output, now in two blocks).  Both parts are recorded as observations."""
    first, second = _split_parts(block.text, at_break)
    taken = {b.id for b in state.blocks}
    n = 1
    while f"{block.id}-s{n}" in taken:
        n += 1
    new_id = f"{block.id}-s{n}"
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    style = chosen.style if chosen is not None else None
    marks = list(chosen.marks) if chosen is not None else []  # each part keeps the marks; rendering finds its own

    def part(block_id: str, text: str) -> Observation:
        number = sum(1 for o in block.observations if o.task == TaskKind.SPLIT) + 1 if block_id == block.id else 1
        return Observation(id=ids.observation_id(block_id, "split", number), engine="split", engine_version=actor,
                           task=TaskKind.SPLIT, anchor=block.anchors[0], text=text, style=style, marks=marks,
                           status=ObservationStatus.OK)

    kept = part(block.id, first)
    block.observations.append(kept)
    block.chosen_observation, block.text = kept.id, first
    rest = part(new_id, second)
    decision = Decision(stage=DecisionStage.STRUCTURE, choice="split", reason=reason, evidence={"at_break": at_break},
                        actor=actor, refs=[block.id, new_id])
    new = Block(id=new_id, kind=BlockKind.TEXT, order=block.order, anchors=list(block.anchors), observations=[rest],
                chosen_observation=rest.id, text=second, decisions=[decision])
    block.decisions.append(decision)
    sequence = ordered(state)
    sequence.insert(next(i for i, b in enumerate(sequence) if b.id == block.id) + 1, new)
    state.blocks.append(new)
    share_containers(state, block.id, [new_id])
    for order, item in enumerate(sequence):
        item.order = order


def _set_excluded(state: DocumentState, block: Block, exclude: bool, reason: str, actor: str, evidence: dict) -> None:
    """Exclude a block or restore it; its ledger entries and image record follow (text stays in the sidecar)."""
    block.status = BlockStatus.EXCLUDED if exclude else BlockStatus.OK
    for entry in state.ledger:
        if entry.block == block.id:
            if exclude and entry.disposition in ("output", "merged"):
                entry.disposition = "excluded"
            elif not exclude and entry.disposition == "excluded":
                entry.disposition = "output"
    assets = {a.asset for a in block.anchors if isinstance(a, AssetAnchor)}
    for record in state.images:
        if record.id in assets:
            record.shown = not exclude
    block.decisions.append(Decision(stage=DecisionStage.EXCLUDE, choice="excluded" if exclude else "restored",
                                    reason=reason, evidence=evidence, actor=actor))
