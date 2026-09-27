"""Selection step (guide §6.3, Q20): scan pages replace failed native layers; the review acceptance gate."""

from parserx.content.scan import PageScanResult
from parserx.content.select import (
    integrate_scan_page,
    mark_scan_failed,
    review_table,
    review_text,
)
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState, LedgerEntry, PageState
from parserx.tables import Cell, TableGrid


def _pdf(page, y=0):
    return PdfAnchor(page=page, bbox=(0, y, 100, y + 10), coord_space="page_pt")


def _obs(oid, engine, *, text=None, cells=None, task=TaskKind.EXTRACT, raw_ref=None, anchor=None):
    return Observation(id=oid, engine=engine, engine_version="v", task=task, anchor=anchor or _pdf(1),
                       text=text, cells=cells, raw_ref=raw_ref, status=ObservationStatus.OK)


def _grid(rows):
    return TableGrid(n_rows=len(rows), n_cols=len(rows[0]),
                     cells=[Cell(row=r, col=c, content=v) for r, row in enumerate(rows) for c, v in enumerate(row)])


# ── Scan pages ──────────────────────────────────────────────────────────


def _state_with_scan_page():
    native = Block(id="b-p002-0001", kind=BlockKind.TEXT, order=1, anchors=[_pdf(2, 100)], text="错字层",
                   observations=[_obs("o-n", "native_pdf", text="错字层")], chosen_observation="o-n")
    scan = Block(id="b-p002-0002", kind=BlockKind.SCAN, order=2,
                 anchors=[_pdf(2), AssetAnchor(asset="a-1", bbox=(0, 0, 10, 10), image_size=(10, 10))])
    other_page = Block(id="b-p001-0001", kind=BlockKind.TEXT, order=0, anchors=[_pdf(1)], text="第一页")
    return DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE),
               PageState(n=2, unit="pdf_page", status=PageStatus.PENDING)],
        blocks=[other_page, native, scan],
        ledger=[LedgerEntry(item="i-p002-00001", unit="native_line", source=_pdf(2, 100), chars=3,
                            disposition="output", block=native.id),
                LedgerEntry(item="i-p002-00002", unit="pdf_image", source=_pdf(2), chars=0,
                            disposition="output", block=scan.id)],
    )


def _ocr_result():
    block = Block(id="b-p002-0003", kind=BlockKind.TEXT, order=0, anchors=[_pdf(2, 98)], text="正字层",
                  observations=[_obs("o-s", "paddleocr", text="正字层", task=TaskKind.RECOGNIZE)],
                  chosen_observation="o-s")
    return PageScanResult(blocks=[block], ledger=[LedgerEntry(
        item="i-p002-00003", unit="ocr_block", source=_pdf(2, 98), chars=3, disposition="output", block=block.id)])


def test_scan_result_replaces_the_failed_native_layer():
    state = _state_with_scan_page()
    integrate_scan_page(state, 2, _ocr_result())
    by_id = {b.id: b for b in state.blocks}
    assert by_id["b-p002-0001"].status == BlockStatus.DUPLICATE
    assert by_id["b-p002-0002"].status == BlockStatus.MERGED
    assert by_id["b-p002-0003"].status == BlockStatus.OK
    assert [(r.kind, r.src, r.dst) for r in state.relations] == [("duplicate_of", "b-p002-0001", "b-p002-0003")]
    assert {e.block: e.disposition for e in state.ledger} == {
        "b-p002-0001": "duplicate", "b-p002-0002": "merged", "b-p002-0003": "output"}
    assert state.pages[1].status == PageStatus.DONE
    assert [b.id for b in sorted(state.blocks, key=lambda b: b.order)] == [
        "b-p001-0001", "b-p002-0001", "b-p002-0002", "b-p002-0003"]
    assert [b.order for b in sorted(state.blocks, key=lambda b: b.order)] == [0, 1, 2, 3]
    assert by_id["b-p002-0001"].decisions[-1].stage == "content_source"


def test_failed_scan_keeps_the_fallbacks_and_reports_missing():
    state = _state_with_scan_page()
    mark_scan_failed(state, 2, "TransientError: 503", skipped=False)
    by_id = {b.id: b for b in state.blocks}
    assert state.pages[1].status == PageStatus.FAILED
    assert by_id["b-p002-0001"].status == BlockStatus.DEGRADED  # fallback text stays visible
    assert by_id["b-p002-0002"].status == BlockStatus.OK  # the scan image stays visible
    assert [m.block for m in state.missing] == ["b-p002-0002"] and "503" in state.missing[0].reason
    mark_scan_failed(state, 2, "budget", skipped=True)
    assert state.pages[1].status == PageStatus.SKIPPED


# ── Acceptance gate: tables ─────────────────────────────────────────────


def _table_block(engine="paddleocr"):
    grid = _grid([["项目", "数值"], ["甲", "3"], ["乙", "20"]])
    return Block(id="b-t", kind=BlockKind.TABLE, order=0, anchors=[_pdf(1)], cells=grid,
                 observations=[_obs("o-base", engine, cells=grid)], chosen_observation="o-base")


def _candidate(rows, *, raw_ref="r" * 64, anchor=None):
    image_anchor = anchor or AssetAnchor(asset="a-crop", bbox=(0, 0, 10, 10), image_size=(10, 10))
    return _obs("o-review", "vlm", cells=_grid(rows), task=TaskKind.REVIEW, raw_ref=raw_ref, anchor=image_anchor)


def test_asked_digit_fix_is_adopted():
    block = _table_block()
    outcome = review_table(block, _candidate([["项目", "数值"], ["甲", "8"], ["乙", "20"]]),
                           allowed_cells={(1, 1)}, actor="tool:review_table")
    assert outcome.adopted and all(g.passed for g in outcome.gate)
    assert block.chosen_observation == "o-review" and block.cells.slot(1, 1).content == "8"
    assert block.decisions[-1].stage == "review_accept" and block.decisions[-1].choice == "adopted"


def test_number_changed_outside_the_asked_cells_is_rejected():
    block = _table_block()
    outcome = review_table(block, _candidate([["项目", "数值"], ["甲", "3"], ["乙", "90"]]),
                           allowed_cells={(1, 1)}, actor="tool:review_table")
    assert not outcome.adopted
    assert {g.name: g.passed for g in outcome.gate}["numeric_consistency"] is False
    assert block.chosen_observation == "o-base" and block.cells.slot(2, 1).content == "20"
    assert any(o.id == "o-review" for o in block.observations)  # the rejected candidate stays as evidence
    assert block.decisions[-1].choice == "rejected"


def test_native_numbers_are_never_overwritten():
    block = _table_block(engine="native_pdf")
    outcome = review_table(block, _candidate([["项目", "数值"], ["甲", "8"], ["乙", "20"]]),
                           allowed_cells={(1, 1)}, actor="tool:review_table")
    assert not outcome.adopted


def test_structure_fix_may_move_but_not_lose_content():
    block = _table_block()
    moved = _candidate([["项目", "数值"], ["乙", "20"], ["甲", "3"]])  # rows reordered: same content
    assert review_table(block, moved, allowed_cells=set(), actor="t").adopted
    block = _table_block()
    lost = _candidate([["项目", "数值"], ["甲", "3"], ["", ""]])
    outcome = review_table(block, lost, allowed_cells=set(), actor="t")
    assert not outcome.adopted and {g.name: g.passed for g in outcome.gate}["structure_valid"] is False


def _fused_table_block():
    # an unruled table read as one row: the first column's three lines in one cell (Phase 3, paper01)
    grid = _grid([["类别", "例子"], ["逐元素运算 数组运算 矩阵运算", "Add, Sub Concat, Slice MatMul"]])
    return Block(id="b-t", kind=BlockKind.TABLE, order=0, anchors=[_pdf(1)], cells=grid,
                 observations=[_obs("o-base", "native_pdf", cells=grid)], chosen_observation="o-base")


def test_splitting_a_cell_into_rows_keeps_content():
    split = _candidate([["类别", "例子"], ["逐元素运算", "Add, Sub"], ["数组运算", "Concat, Slice"],
                        ["矩阵运算", "MatMul"]])
    outcome = review_table(_fused_table_block(), split, allowed_cells=set(), actor="t")
    assert outcome.adopted, outcome.gate


def test_a_split_that_drops_or_alters_text_is_rejected():
    dropped = _candidate([["类别", "例子"], ["逐元素运算", "Add, Sub"], ["数组运算", "Concat, Slice"], ["", "MatMul"]])
    altered = _candidate([["类别", "例子"], ["逐元素运算", "Add, Sub"], ["数组运", "Concat, Slice"], ["矩阵运算", "MatMul"]])
    for candidate in (dropped, altered):
        outcome = review_table(_fused_table_block(), candidate, allowed_cells=set(), actor="t")
        assert not outcome.adopted and {g.name: g.passed for g in outcome.gate}["structure_valid"] is False


def test_merging_cells_keeps_content_but_each_repeated_cell_must_survive():
    def block():
        grid = _grid([["名称", "说明"], ["甲", "第一"], ["", "项"], ["乙", "同上"], ["丙", "同上"]])
        return Block(id="b-t", kind=BlockKind.TABLE, order=0, anchors=[_pdf(1)], cells=grid,
                     observations=[_obs("o-base", "native_pdf", cells=grid)], chosen_observation="o-base")

    merged = _candidate([["名称", "说明"], ["甲", "第一项"], ["乙", "同上"], ["丙", "同上"]])
    assert review_table(block(), merged, allowed_cells=set(), actor="t").adopted
    once = _candidate([["名称", "说明"], ["甲", "第一项"], ["乙", "同上"], ["丙", ""]])  # one "同上" gone
    assert not review_table(block(), once, allowed_cells=set(), actor="t").adopted


def test_numbers_and_cells_are_compared_in_one_notation():
    # a head drawn as k with a subscript, read as two rows by the text layer, merged back by the reading: k₁ is k1
    grid = _grid([["方法", "k", "k"], ["", "1", "2"], ["方法 A", "0.03", "-0.42"]])
    merged = _candidate([["方法", "k₁", "k₂"], ["方法 A", "0.03", "-0.42"]])
    for engine in ("native_pdf", "paddleocr"):
        block = Block(id="b-t", kind=BlockKind.TABLE, order=0, anchors=[_pdf(1)], cells=grid,
                      observations=[_obs("o-base", engine, cells=grid)], chosen_observation="o-base")
        # the structure issue names the head: merged cells are content the table has, not fills from the image
        outcome = review_table(block, merged, allowed_cells=set(), fill_region={(0, 1), (0, 2), (1, 1), (1, 2)},
                               actor="t")
        assert outcome.adopted, (engine, outcome.gate)


def test_a_missing_column_is_filled_only_where_a_structure_issue_named_it():
    # Q45: cells the OCR missed may come from the image alone — inside the region a structure issue names
    with_column = [["项目", "单价", "数值"], ["甲", "5", "3"], ["乙", "7", "20"]]
    block = _table_block()
    outcome = review_table(block, _candidate(with_column), allowed_cells=set(), fill_region={(1, 1), (2, 1)},
                           actor="t")
    assert outcome.adopted and block.cells.n_cols == 3
    assert block.decisions[-1].evidence["image_only_cells"] == "r1c1,r2c1"  # marked in the sidecar
    block = _table_block()  # a fill is new text even when it reads inside a number the table has ("2" in "20")
    assert review_table(block, _candidate([["项目", "单价", "数值"], ["甲", "2", "3"], ["乙", "7", "20"]]),
                        allowed_cells=set(), fill_region={(1, 1), (2, 1)}, actor="t").adopted
    block = _table_block()
    outcome = review_table(block, _candidate(with_column), allowed_cells=set(), actor="t")
    detail = {g.name: g.detail for g in outcome.gate}["numeric_consistency"]
    assert not outcome.adopted and "(1, 1)" in detail and "structure issue" in detail
    block = _table_block()  # a fill does not cover a changed number: 20 would be lost
    changed = [["项目", "单价", "数值"], ["甲", "5", "3"], ["乙", "7", "26"]]
    assert not review_table(block, _candidate(changed), allowed_cells=set(), fill_region={(1, 1), (2, 1)},
                            actor="t").adopted
    block = _table_block(engine="native_pdf")  # the native layer is exact: nothing is filled from the image
    assert not review_table(block, _candidate(with_column), allowed_cells=set(), fill_region={(1, 1), (2, 1)},
                            actor="t").adopted


def test_candidate_without_image_evidence_is_rejected():
    block = _table_block()
    blind = _candidate([["项目", "数值"], ["甲", "8"], ["乙", "20"]], raw_ref=None)
    outcome = review_table(block, blind, allowed_cells={(1, 1)}, actor="t")
    assert not outcome.adopted and {g.name: g.passed for g in outcome.gate}["image_evidence"] is False


# ── Acceptance gate: text (guide §11.5 first requirement) ──────────────


def test_text_candidate_cannot_rewrite_numbers_against_evidence():
    block = Block(id="b-x", kind=BlockKind.TEXT, order=0, anchors=[_pdf(1)], text="采购金额为 100 万元。",
                  observations=[_obs("o-ocr", "paddleocr", text="采购金额为 100 万元。")],
                  chosen_observation="o-ocr")
    candidate = _obs("o-vlm", "vlm", text="采购金额为 999 万元。", task=TaskKind.REVIEW, raw_ref="r" * 64,
                     anchor=AssetAnchor(asset="a", bbox=(0, 0, 1, 1), image_size=(1, 1)))
    outcome = review_text(block, candidate, actor="t")
    assert not outcome.adopted and block.text == "采购金额为 100 万元。"
    fixed = _obs("o-vlm2", "vlm", text="采购金额为 100 万元整。", task=TaskKind.REVIEW, raw_ref="r" * 64,
                 anchor=AssetAnchor(asset="a", bbox=(0, 0, 1, 1), image_size=(1, 1)))
    assert review_text(block, fixed, actor="t").adopted and block.text == "采购金额为 100 万元整。"


def test_decisions_reference_the_candidate():
    block = _table_block()
    review_table(block, _candidate([["项目", "数值"], ["甲", "8"], ["乙", "20"]]), allowed_cells={(1, 1)}, actor="a")
    decision: Decision = block.decisions[-1]
    assert decision.refs == ["o-review", "o-base"] and decision.actor == "a"
    assert decision.evidence["numeric_consistency"] is True
