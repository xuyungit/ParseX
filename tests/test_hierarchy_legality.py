"""apply_structure legality (guide §6.8, interfaces §5.7): every rule has a rejected example."""

import pytest
from pydantic import TypeAdapter, ValidationError

from parserx.hierarchy import StructureChange, apply_changes, check_changes, numbering_signature
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState
from parserx.tables import Cell, TableGrid

CHANGE = TypeAdapter(list[StructureChange])


def _b(bid, order, kind=BlockKind.TEXT, text="", level=None, **kw):
    return Block(id=bid, kind=kind, order=order, anchors=[PdfAnchor(page=1, bbox=(0, order, 1, order + 1),
                                                                     coord_space="page_pt")],
                 text=text, level=level, **kw)


def _state():
    grid = TableGrid(n_rows=1, n_cols=1, cells=[Cell(row=0, col=0, content="x")])
    return DocumentState(id="d", source="x", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                         blocks=[_b("h1", 0, BlockKind.TITLE, "第一章 总则", level=1),
                                 _b("p1", 1, text="1.1 范围"),
                                 _b("p2", 2, text="正文"),
                                 _b("p3", 3, text="1.2 术语"),
                                 _b("t", 4, BlockKind.TABLE, cells=grid)],
                         relations=[Relation(id="r-follows-p1-p2", kind="follows", src="p1", dst="p2")])


def _changes(*items):
    return CHANGE.validate_python(list(items))


def _rules(changes):
    return [(r.index, r.rule) for r in check_changes(_state(), _changes(*changes))]


def test_legal_batch_is_accepted_and_applied():
    state = _state()
    changes = _changes({"op": "set_role", "block": "p1", "kind": "title", "reason": "numbered heading"},
                       {"op": "set_level", "block": "p1", "level": 2, "reason": "under chapter"})
    assert check_changes(state, changes) == []
    outcome = apply_changes(state, changes, actor="adapter:v1")
    assert outcome.accepted == [0, 1] and outcome.rejected == []
    p1 = next(b for b in state.blocks if b.id == "p1")
    assert (p1.kind, p1.level, p1.text) == (BlockKind.TITLE, 2, "1.1 范围")
    assert [d.stage for d in p1.decisions] == ["heading_role", "heading_level"]
    assert p1.decisions[0].actor == "adapter:v1"


def test_unknown_block():
    assert _rules([{"op": "set_role", "block": "nope", "kind": "title", "reason": "r"}]) == [(0, "unknown_block")]


def test_content_kinds_cannot_change_role():
    assert _rules([{"op": "set_role", "block": "t", "kind": "text", "reason": "r"}]) == [(0, "kind_not_structural")]
    with pytest.raises(ValidationError):  # and nothing can become a table: the request schema has no such kind
        _changes({"op": "set_role", "block": "p1", "kind": "table", "reason": "r"})


def test_level_only_on_titles():
    assert _rules([{"op": "set_level", "block": "p2", "level": 2, "reason": "r"}]) == [(0, "level_on_non_title")]


def test_level_skip():
    assert _rules([{"op": "set_role", "block": "p1", "kind": "title", "reason": "r"},
                   {"op": "set_level", "block": "p1", "level": 3, "reason": "r"}]) == [(1, "level_skip")]


def test_same_numbering_pattern_same_level():
    assert numbering_signature("1.1 范围") == numbering_signature("1.2 术语") == "N.N"
    assert numbering_signature("第三章 结构") == "第N章" and numbering_signature("正文") is None
    assert numbering_signature("1 Scope") == numbering_signature("2. Terms") == "N"
    assert numbering_signature("2024 年度报告") is None and numbering_signature("3") is None
    rules = _rules([{"op": "set_role", "block": "p1", "kind": "title", "reason": "r"},
                    {"op": "set_level", "block": "p1", "level": 2, "reason": "r"},
                    {"op": "set_role", "block": "p3", "kind": "title", "reason": "r"},
                    {"op": "set_level", "block": "p3", "level": 1, "reason": "r"}])
    assert rules == [(3, "numbering_level_inconsistent")]


def test_order_cycle():
    assert _rules([{"op": "move_after", "block": "p1", "after": "p2", "reason": "r"},
                   {"op": "move_after", "block": "p2", "after": "p1", "reason": "r"}]) == [(1, "order_cycle")]
    assert _rules([{"op": "move_after", "block": "p1", "after": "p1", "reason": "r"}]) == [(0, "order_cycle")]


def test_duplicate_relation():
    assert _rules([{"op": "add_relation", "kind": "follows", "src": "p1", "dst": "p2"}]) == [
        (0, "duplicate_relation")]


def test_atomic_batch_applies_nothing_on_any_rejection():
    state = _state()
    before = state.model_copy(deep=True)
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "kind": "title", "reason": "r"},
                                            {"op": "set_level", "block": "p2", "level": 2, "reason": "r"}),
                            actor="agent", atomic=True)
    assert outcome.accepted == [] and [r.rule for r in outcome.rejected] == ["level_on_non_title"]
    assert state == before


def test_non_atomic_batch_applies_the_legal_part():
    state = _state()
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "kind": "title", "reason": "r"},
                                            {"op": "set_level", "block": "p2", "level": 2, "reason": "r"}),
                            actor="agent")
    assert outcome.accepted == [0] and next(b for b in state.blocks if b.id == "p1").kind == BlockKind.TITLE


def test_move_mark_pending_and_relations_apply():
    state = _state()
    apply_changes(state, _changes({"op": "move_after", "block": "p3", "after": "h1", "reason": "r"},
                                  {"op": "mark_pending", "block": "p2", "reason": "unclear role"},
                                  {"op": "remove_relation", "relation": "r-follows-p1-p2"},
                                  {"op": "add_relation", "kind": "follows", "src": "p3", "dst": "p1"}), actor="agent")
    assert [b.id for b in sorted(state.blocks, key=lambda b: b.order)] == ["h1", "p3", "p1", "p2", "t"]
    p2 = next(b for b in state.blocks if b.id == "p2")
    assert p2.status == BlockStatus.DEGRADED and p2.text == "正文"
    assert [r.id for r in state.relations] == ["r-follows-p3-p1"]


def test_requests_carry_no_text_field():
    for model in StructureChange.__args__[0].__args__:
        assert not {"text", "content"} & set(model.model_fields)


def test_appendix_numbering_has_its_own_signature():
    # "C.1" is appendix C, section 1: neither a Roman numeral nor the pattern of chapter "1".
    assert numbering_signature("C.1 一般规定") == numbering_signature("A.2 材料") == "L.N"
    assert numbering_signature("C.0.1 说明") == "L.N.N" and numbering_signature("1 总则") == "N"
    # letter sequences share one pattern; I, V, X are Roman numerals
    assert numbering_signature("A. 概述") == numbering_signature("B. 方法") == numbering_signature("C. 结果") == "L"
    assert numbering_signature("I. Introduction") == numbering_signature("IV. 结论") == numbering_signature("V. 讨论") == "N"
    assert numbering_signature("a) 前提") == numbering_signature("b) 条件") == "L)"


# ── P2-5: role with level, exclude and restore ──────────────────────────


def test_set_role_to_title_may_carry_its_level():
    state = _state()
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "kind": "title", "level": 2,
                                             "reason": "numbered heading"}), actor="agent")
    p1 = next(b for b in state.blocks if b.id == "p1")
    assert outcome.accepted == [0] and (p1.kind, p1.level) == (BlockKind.TITLE, 2)
    assert [d.stage for d in p1.decisions] == ["heading_role", "heading_level"]
    assert _rules([{"op": "set_role", "block": "p1", "kind": "title", "level": 3, "reason": "r"}]) == [(0, "level_skip")]
    assert _rules([{"op": "set_role", "block": "p1", "kind": "text", "level": 2, "reason": "r"}]) == \
        [(0, "level_on_non_title")]


def _ledgered_state():
    from parserx.ir.state import LedgerEntry

    state = _state()
    anchor = PdfAnchor(page=1, bbox=(0, 0, 1, 1), coord_space="page_pt")
    state.ledger = [LedgerEntry(item=f"i-{b.id}", unit="native_line", source=anchor, chars=2, disposition="output",
                                block=b.id) for b in state.blocks]
    return state


def test_exclude_keeps_the_text_and_accounts_for_it():
    from parserx.accounting import check

    state = _ledgered_state()
    outcome = apply_changes(state, _changes({"op": "exclude", "block": "p2", "reason": "图标被识成的字符"}),
                            actor="agent")
    p2 = next(b for b in state.blocks if b.id == "p2")
    assert outcome.accepted == [0] and p2.status == BlockStatus.EXCLUDED and p2.text == "正文"
    assert p2.decisions[-1].stage == "exclude" and p2.decisions[-1].actor == "agent"
    assert next(e for e in state.ledger if e.block == "p2").disposition == "excluded"
    result = check(state)
    assert result.mismatched == [] and result.accounting.excluded == 1
    assert _rules([{"op": "exclude", "block": "p2", "reason": ""}]) == [(0, "reason_required")]


def test_restore_undoes_an_exclusion_but_not_a_deleted_revision():
    from parserx.ir.decision import Decision

    state = _ledgered_state()
    apply_changes(state, _changes({"op": "exclude", "block": "p2", "reason": "r"}), actor="agent")
    outcome = apply_changes(state, _changes({"op": "restore", "block": "p2", "reason": "是正文"}), actor="agent")
    p2 = next(b for b in state.blocks if b.id == "p2")
    assert outcome.accepted == [0] and p2.status == BlockStatus.OK
    assert next(e for e in state.ledger if e.block == "p2").disposition == "output"
    assert check_changes(state, _changes({"op": "restore", "block": "p2", "reason": "r"}))[0].rule == "not_excluded"
    p3 = next(b for b in state.blocks if b.id == "p3")
    p3.status = BlockStatus.EXCLUDED
    p3.decisions.append(Decision(stage="exclude", choice="revision_deleted", reason="deleted", evidence={},
                                 actor="program:content.docx"))
    assert check_changes(state, _changes({"op": "restore", "block": "p3", "reason": "r"}))[0].rule == "not_restorable"


def test_a_title_with_a_level_can_become_text_again():
    state = _state()
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "h1", "kind": "text", "reason": "封面标识"}),
                            actor="agent")
    h1 = next(b for b in state.blocks if b.id == "h1")
    assert outcome.accepted == [0] and (h1.kind, h1.level) == (BlockKind.TEXT, None)
