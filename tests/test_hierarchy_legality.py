"""Structure changes and their legality (guide §6.8, Q86): every rule has a rejected example."""

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
                         relations=[Relation(id="r-continues-p1-p2", kind="continues", src="p1", dst="p2")])


def _changes(*items):
    return CHANGE.validate_python(list(items))


def _rules(changes):
    return [(r.index, r.rule) for r in check_changes(_state(), _changes(*changes))]


def test_legal_batch_is_accepted_and_applied():
    state = _state()
    changes = _changes({"op": "set_role", "block": "p1", "role": "H2", "reason": "numbered heading under chapter"})
    assert check_changes(state, changes) == []
    outcome = apply_changes(state, changes, actor="program:hierarchy.typography")
    assert outcome.accepted == [0] and outcome.rejected == []
    p1 = next(b for b in state.blocks if b.id == "p1")
    assert (p1.kind, p1.level, p1.text) == (BlockKind.TITLE, 2, "1.1 范围")
    assert [d.stage for d in p1.decisions] == ["heading_role", "heading_level"]
    assert p1.decisions[0].actor == "program:hierarchy.typography"


def test_unknown_block():
    assert _rules([{"op": "set_role", "block": "nope", "role": "H1", "reason": "r"}]) == [(0, "unknown_block")]


def test_content_kinds_cannot_change_role():
    assert _rules([{"op": "set_role", "block": "t", "role": "text", "reason": "r"}]) == [(0, "kind_not_structural")]
    # nothing can become a table, a title has a level, page furniture is not a role: the schema has no such role
    for role in ("table", "title", "H7", "header", "page_number"):
        with pytest.raises(ValidationError):
            _changes({"op": "set_role", "block": "p1", "role": role, "reason": "r"})


def test_level_skip():
    assert _rules([{"op": "set_role", "block": "p1", "role": "H3", "reason": "r"}]) == [(0, "level_skip")]


def test_same_numbering_pattern_same_level():
    assert numbering_signature("1.1 范围") == numbering_signature("1.2 术语") == "N.N"
    assert numbering_signature("第三章 结构") == "第N章" and numbering_signature("正文") is None
    assert numbering_signature("1 Scope") == numbering_signature("2. Terms") == "N"
    assert numbering_signature("2024 年度报告") is None and numbering_signature("3") is None
    rules = _rules([{"op": "set_role", "block": "p1", "role": "H2", "reason": "r"},
                    {"op": "set_role", "block": "p3", "role": "H1", "reason": "r"}])
    assert rules == [(1, "numbering_level_inconsistent")]


def test_a_convention_is_broken_only_on_evidence():
    from parserx.ir.evidence import Evidence

    # 1.2 set one level above 1.1: the numbering rule refuses, unless the change overrides it on evidence
    changes = [{"op": "set_role", "block": "p1", "role": "H2", "reason": "r"},
               {"op": "set_role", "block": "p3", "role": "H1", "reason": "原件上 1.2 单独成章", "override": True}]
    assert _rules(changes) == [(1, "override_without_evidence")]
    state = _state()
    state.evidence.append(Evidence(id="e-000000000001", how="image", page=1, image="a-1"))
    changes[1]["evidence"] = "e-000000000001"
    outcome = apply_changes(state, _changes(*changes), actor="agent")
    assert outcome.rejected == [] and next(b for b in state.blocks if b.id == "p3").decisions[-1].evidence == \
        {"evidence": "e-000000000001", "override": True}
    changes[1]["role"] = "H4"  # no skipped level (H2 → H4), whatever the evidence: an output convention
    assert [r.rule for r in check_changes(_state_with(state.evidence), _changes(*changes))] == ["level_skip"]


def _state_with(evidence):
    state = _state()
    state.evidence = list(evidence)
    return state


def test_order_cycle():
    assert _rules([{"op": "move", "block": "p1", "after": "p2", "reason": "r"},
                   {"op": "move", "block": "p2", "after": "p1", "reason": "r"}]) == [(1, "order_cycle")]
    assert _rules([{"op": "move", "block": "p1", "after": "p1", "reason": "r"}]) == [(0, "order_cycle")]


def test_join_joins_two_paragraphs_and_unjoin_undoes_it():
    assert _rules([{"op": "join", "first": "p1", "second": "p2", "reason": "r"}]) == [(0, "already_joined")]
    assert _rules([{"op": "join", "first": "h1", "second": "p1", "reason": "r"}]) == [(0, "not_joinable")]  # a title
    assert _rules([{"op": "join", "first": "p3", "second": "t", "reason": "r"}]) == [(0, "not_joinable")]
    assert _rules([{"op": "join", "first": "p2", "second": "p3", "drop_rows": 1, "reason": "r"}]) == \
        [(0, "not_joinable")]  # drop_rows is for tables
    assert _rules([{"op": "unjoin", "first": "p2", "second": "p3", "reason": "r"}]) == [(0, "not_joined")]
    state = _state()
    outcome = apply_changes(state, _changes({"op": "unjoin", "first": "p1", "second": "p2", "reason": "两段"},
                                            {"op": "join", "first": "p2", "second": "p3", "reason": "分页断开"}),
                            actor="agent")
    assert outcome.accepted == [0, 1] and [r.id for r in state.relations] == ["r-continues-p2-p3"]
    assert [d.choice for d in next(b for b in state.blocks if b.id == "p3").decisions] == ["join"]


def test_atomic_batch_applies_nothing_on_any_rejection():
    state = _state()
    before = state.model_copy(deep=True)
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "role": "H2", "reason": "r"},
                                            {"op": "set_role", "block": "p2", "role": "H4", "reason": "r"}),
                            actor="agent", atomic=True)
    assert outcome.accepted == [] and [r.rule for r in outcome.rejected] == ["level_skip"]
    assert state == before


def test_non_atomic_batch_applies_the_legal_part():
    state = _state()
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "role": "H2", "reason": "r"},
                                            {"op": "set_role", "block": "p2", "role": "H4", "reason": "r"}),
                            actor="agent")
    assert outcome.accepted == [0] and next(b for b in state.blocks if b.id == "p1").kind == BlockKind.TITLE


def test_move_and_mark_pending_apply():
    state = _state()
    apply_changes(state, _changes({"op": "move", "block": "p3", "after": "h1", "reason": "r"},
                                  {"op": "mark_pending", "block": "p2", "reason": "unclear role"}), actor="agent")
    assert [b.id for b in sorted(state.blocks, key=lambda b: b.order)] == ["h1", "p3", "p1", "p2", "t"]
    p2 = next(b for b in state.blocks if b.id == "p2")
    assert p2.status == BlockStatus.DEGRADED and p2.text == "正文"


def test_requests_carry_no_text_field():
    for model in StructureChange.__args__[0].__args__:
        assert not {"text", "content"} & set(model.model_fields)


def test_appendix_numbering_has_its_own_signature():
    # "C.1" is appendix C, section 1: neither a Roman numeral nor the pattern of chapter "1".
    assert numbering_signature("C.1 一般规定") == numbering_signature("A.2 材料") == "L.N"
    assert numbering_signature("C.0.1 说明") == "L.N.N" and numbering_signature("1 总则") == "N"
    # letter sequences share one pattern; I, V, X are Roman numerals
    assert numbering_signature("A. 概述") == numbering_signature("B. 方法") == numbering_signature("C. 结果") == "L"
    assert numbering_signature("I. Introduction") == numbering_signature("IV. 结论") == numbering_signature("V. 讨论") == "R"
    assert numbering_signature("a) 前提") == numbering_signature("b) 条件") == "L)"


# ── P2-5: role with level, exclude and restore ──────────────────────────


def test_page_furniture_given_a_body_role_or_included_comes_back():
    state = _ledgered_state()
    for block in state.blocks[1:3]:  # p1, p2: running headers the program left out
        block.kind, block.status = BlockKind.HEADER, BlockStatus.EXCLUDED
        next(e for e in state.ledger if e.block == block.id).disposition = "excluded"
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "p1", "role": "H2", "reason": "节标题"},
                                            {"op": "include", "block": "p2", "reason": "是正文"}), actor="agent")
    p1, p2 = state.blocks[1:3]
    assert outcome.accepted == [0, 1]
    assert (p1.kind, p1.level, p1.status) == (BlockKind.TITLE, 2, BlockStatus.OK)
    assert (p2.kind, p2.status) == (BlockKind.TEXT, BlockStatus.OK)
    assert {e.disposition for e in state.ledger if e.block in ("p1", "p2")} == {"output"}


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
    outcome = apply_changes(state, _changes({"op": "include", "block": "p2", "reason": "是正文"}), actor="agent")
    p2 = next(b for b in state.blocks if b.id == "p2")
    assert outcome.accepted == [0] and p2.status == BlockStatus.OK
    assert next(e for e in state.ledger if e.block == "p2").disposition == "output"
    assert check_changes(state, _changes({"op": "include", "block": "p2", "reason": "r"}))[0].rule == "not_excluded"
    p3 = next(b for b in state.blocks if b.id == "p3")
    p3.status = BlockStatus.EXCLUDED
    p3.decisions.append(Decision(stage="exclude", choice="revision_deleted", reason="deleted", evidence={},
                                 actor="program:content.docx"))
    assert check_changes(state, _changes({"op": "include", "block": "p3", "reason": "r"}))[0].rule == "not_restorable"


def test_a_title_with_a_level_can_become_text_again():
    state = _state()
    outcome = apply_changes(state, _changes({"op": "set_role", "block": "h1", "role": "text", "reason": "封面标识"}),
                            actor="agent")
    h1 = next(b for b in state.blocks if b.id == "h1")
    assert outcome.accepted == [0] and (h1.kind, h1.level) == (BlockKind.TEXT, None)


def _titled(*titles):
    blocks = [_b(f"t{i}", i, BlockKind.TITLE, text, level=level) for i, (text, level) in enumerate(titles)]
    return DocumentState(id="d", source="x", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                         blocks=blocks)


def test_numbering_consistency_holds_within_the_same_section_only():
    # Q43: an attachment that restarts its numbering is not compared with the main text
    state = _titled(("1 总则", 1), ("1.1 范围", 2), ("1.1.1 适用", 3), ("2 术语", 1), ("附件3 检测报告", 1),
                    ("1 概述", None), ("1.2 定义", None))
    rules = [(r.index, r.rule) for r in check_changes(state, _changes(
        {"op": "set_role", "block": "t5", "role": "H2", "reason": "附件中另起的编号"},
        {"op": "set_role", "block": "t6", "role": "H3", "reason": "附件里的小节"}))]
    assert rules == []
    # within the main text, 1.2 must be the level of 1.1
    state = _titled(("1 总则", 1), ("1.1 范围", 2), ("1.1.1 适用", 3), ("1.2 定义", None), ("2 术语", 1))
    assert [r.rule for r in check_changes(state, _changes(
        {"op": "set_role", "block": "t3", "role": "H3", "reason": "r"}))] == ["numbering_level_inconsistent"]


def test_titles_read_inside_an_image_number_on_their_own():
    # an embedded scanned document ("一、" inside the image) is not held to the main text's "一、" at H1
    state = _titled(("一、总则", 1), ("二、要求", 1), ("附件", 2), ("一、概况", None), ("二、结论", None))
    state.blocks.append(_b("fig", 99, BlockKind.FIGURE))
    state.relations = [Relation(id=f"r-contains-fig-t{i}", kind="contains", src="fig", dst=f"t{i}") for i in (3, 4)]
    assert check_changes(state, _changes({"op": "set_role", "block": "t3", "role": "H3", "reason": "图中文件的小节"},
                                         {"op": "set_role", "block": "t4", "role": "H3", "reason": "同上"})) == []
    # inside the image the pattern still has one level
    assert [r.rule for r in check_changes(state, _changes(
        {"op": "set_role", "block": "t3", "role": "H3", "reason": "r"},
        {"op": "set_role", "block": "t4", "role": "H4", "reason": "r"}))] == ["numbering_level_inconsistent"]


def test_split_divides_a_block_at_one_of_its_line_breaks():
    from parserx.accounting import check

    state = _ledgered_state()
    p2 = next(b for b in state.blocks if b.id == "p2")
    p2.text = "6.1 铸钢件\n1) 铸造及热处理"  # a soft line break joined a title and the first item
    outcome = apply_changes(state, _changes({"op": "split", "block": "p2", "at_break": 1,
                                             "reason": "标题与条目只隔了一个软换行"}), actor="agent")
    assert outcome.accepted == [0]
    order = sorted(state.blocks, key=lambda b: b.order)
    i = [b.id for b in order].index("p2")
    first, second = order[i], order[i + 1]
    assert (first.text, second.text) == ("6.1 铸钢件", "1) 铸造及热处理")  # nothing added, nothing lost
    assert second.kind == BlockKind.TEXT and second.anchors == first.anchors
    assert second.decisions[-1].choice == "split" and first.decisions[-1].refs == ["p2", second.id]
    result = check(state)
    assert result.mismatched == [] and result.illegal_refs == []
    assert _rules([{"op": "split", "block": "p2", "at_break": 1, "reason": "r"}]) == [(0, "no_line_break")]
    assert _rules([{"op": "split", "block": "t", "at_break": 1, "reason": "r"}]) == [(0, "kind_not_structural")]


def test_a_part_split_from_text_read_in_an_image_stays_in_the_image():
    # the text after the break is still the figure's transcription (IO6-2)
    from parserx.ir import ids
    from parserx.ir.enums import RelationKind

    state = _ledgered_state()
    p2 = next(b for b in state.blocks if b.id == "p2")
    p2.text = "营业执照\n统一社会信用代码"
    figure = Block(id="fig", kind=BlockKind.FIGURE, order=-1, anchors=list(p2.anchors))
    state.blocks.append(figure)
    state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTAINS, "fig", "p2"), kind=RelationKind.CONTAINS,
                                    src="fig", dst="p2"))
    outcome = apply_changes(state, _changes({"op": "split", "block": "p2", "at_break": 1, "reason": "两行"}),
                            actor="agent")
    assert outcome.accepted == [0]
    new = next(b for b in state.blocks if b.id.startswith("p2-s"))
    assert {r.dst for r in state.relations if r.kind == RelationKind.CONTAINS and r.src == "fig"} == {"p2", new.id}


def test_numeral_classes_are_different_numbering_styles():
    # 一、 > （一） > 1. > （1）: the numerals' class tells the styles apart (P4-3)
    assert numbering_signature("一、总则") == "C、" and numbering_signature("1、目的") == "N、"
    assert numbering_signature("（一）范围") != numbering_signature("（1）说明")
    assert numbering_signature("第一章 总则") == numbering_signature("第3章 结构") == "第N章"  # the unit makes the style
    assert numbering_signature("ii. scope") == "r" and numbering_signature("III. 方法") == "R"


def test_the_chinese_four_level_numbering_is_legal():
    from parserx.ir.anchor import PdfAnchor
    from parserx.ir.block import Block
    from parserx.ir.enums import BlockKind, DocumentStatus, PageStatus
    from parserx.ir.state import DocumentState, PageState

    texts = ["一、总则", "（一）范围", "1. 适用对象", "（1）新建工程", "（2）改建工程", "2. 术语", "（二）要求", "二、方法"]
    blocks = [Block(id=f"t{i}", kind=BlockKind.TEXT, order=i, text=text,
                    anchors=[PdfAnchor(page=1, bbox=(0, 20 * i, 100, 20 * i + 10), coord_space="page_pt")])
              for i, text in enumerate(texts)]
    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)],
                          blocks=blocks)
    levels = [1, 2, 3, 4, 4, 3, 2, 1]
    changes = [{"op": "set_role", "block": f"t{i}", "role": f"H{lv}", "reason": "r"} for i, lv in enumerate(levels)]
    assert apply_changes(state, _changes(*changes), actor="t").rejected == []


def test_the_program_does_not_override_the_agents_structure_decisions():
    # the program's proposals leave alone what the agent decided on a block
    state = _state()
    demote = _changes({"op": "set_role", "block": "h1", "role": "text", "reason": "a contents entry"})
    assert apply_changes(state, demote, actor="agent").accepted == [0]
    again = _changes({"op": "set_role", "block": "h1", "role": "H1", "reason": "typography"},
                     {"op": "set_role", "block": "p1", "role": "H1", "reason": "typography"})
    outcome = apply_changes(state, again, actor="program:hierarchy.typography")
    assert outcome.accepted == [1] and [(r.index, r.rule) for r in outcome.rejected] == [(0, "decided_by_agent")]
    assert next(b for b in state.blocks if b.id == "h1").kind == BlockKind.TEXT
    assert apply_changes(state, again[:1], actor="agent").accepted == [0]  # the agent may change its mind


def _outline():
    # 第一章 H1 / 1.1 H3 / 1.2 H3 / 1.3 H3: the sections sit one level too deep
    return DocumentState(id="d", source="x", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                         blocks=[_b("c", 0, BlockKind.TITLE, "第一章 总则", level=1),
                                 _b("a", 1, BlockKind.TITLE, "1.1 范围", level=2),
                                 _b("s1", 2, BlockKind.TITLE, "1.1.1 目的", level=3),
                                 _b("s2", 3, BlockKind.TITLE, "1.1.2 依据", level=3)])


def test_a_batch_is_judged_by_the_outline_it_produces():
    from parserx.hierarchy import apply_batch

    # moving a whole group: each step alone breaks "one level per numbering pattern", the result does not
    state = _outline()
    changes = _changes(*({"op": "set_role", "block": b, "role": f"H{lv}", "reason": "r"}
                         for b, lv in (("a", 1), ("s1", 2), ("s2", 2))))
    assert [r.rule for r in apply_changes(_outline(), changes, actor="agent").rejected] == \
        ["level_skip", "numbering_level_inconsistent", "numbering_level_inconsistent"]  # step by step
    outcome = apply_batch(state, changes, actor="agent")
    assert outcome.accepted == [0, 1, 2] and outcome.rejected == []
    assert [b.level for b in state.blocks] == [1, 1, 2, 2]


def test_a_batch_refuses_what_is_illegal_in_the_result_and_keeps_the_rest():
    from parserx.hierarchy import apply_batch

    state = _outline()
    changes = _changes({"op": "set_role", "block": "s1", "role": "H2", "reason": "only one of the two siblings"},
                       {"op": "set_role", "block": "c", "role": "H1", "reason": "unchanged, legal"})
    outcome = apply_batch(state, changes, actor="agent")
    assert outcome.accepted == [1] and [(r.index, r.rule) for r in outcome.rejected] == \
        [(0, "numbering_level_inconsistent")]
    assert "s2" in outcome.rejected[0].detail and [b.level for b in state.blocks] == [1, 2, 3, 3]
