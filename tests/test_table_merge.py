"""Cross-page table continuation (guide §6.9): candidates, confirmation, the join of two tables."""

from pydantic import TypeAdapter

from parserx.accounting.check import check
from parserx.hierarchy import StructureChange, apply_changes, check_changes
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, LedgerEntry, PageState
from parserx.render import render_markdown
from parserx.tables import Cell, TableGrid
from parserx.tables.merge import merge_candidates, propose_merges
from parserx.tools.views import unresolved_items

CHANGE = TypeAdapter(list[StructureChange])
HEADER = ["No.", "Item", "Qty"]


def _grid(rows):
    return TableGrid(n_rows=len(rows), n_cols=len(rows[0]),
                     cells=[Cell(row=r, col=c, content=v) for r, row in enumerate(rows) for c, v in enumerate(row)])


def _rows(first, last, header=True):
    return ([HEADER] if header else []) + [[str(i), f"part {i}", str(10 * i)] for i in range(first, last + 1)]


def _block(bid, page, bbox, kind=BlockKind.TEXT, text="", rows=None):
    return Block(id=bid, kind=kind, order=0, anchors=[PdfAnchor(page=page, bbox=bbox, coord_space="page_pt")],
                 text=text, cells=_grid(rows) if rows else None)


def _state(*page_blocks):
    """Blocks per page, in reading order; every block carries one ledger item."""
    blocks = [b for page in page_blocks for b in page]
    for order, block in enumerate(blocks):
        block.order = order
    ledger = [LedgerEntry(item=f"i{n}", unit="native_line", source=b.anchors[0], chars=1, disposition="output",
                          block=b.id) for n, b in enumerate(blocks)]
    pages = [PageState(n=n, unit="pdf_page", status=PageStatus.DONE, size_pt=(600.0, 800.0))
             for n in range(1, len(page_blocks) + 1)]
    return DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf",
                         status=DocumentStatus.IN_PROGRESS, pages=pages, blocks=blocks, ledger=ledger)


def _continued(second_rows, second_bbox=(50, 60, 550, 400), between=()):
    return _state(
        [_block("intro", 1, (50, 50, 550, 70), text="Table 1 Parts"),
         _block("t1", 1, (50, 80, 550, 760), BlockKind.TABLE, rows=_rows(1, 20)),
         _block("pn1", 1, (295, 775, 305, 785), BlockKind.PAGE_NUMBER, text="5")],
        [*between, _block("t2", 2, second_bbox, BlockKind.TABLE, rows=second_rows),
         _block("after", 2, (50, 420, 550, 440), text="Body text after the table.")],
    )


def test_repeated_header_across_a_page_break_is_a_confirmed_candidate():
    [candidate] = merge_candidates(_continued(_rows(21, 30)))
    assert (candidate.first, candidate.second, candidate.drop_rows, candidate.confirmed) == ("t1", "t2", 1, True)
    assert candidate.evidence["header_repeated"] is True and candidate.evidence["columns"] == 3


def test_continuation_without_a_repeated_header_is_confirmed_too():
    [candidate] = merge_candidates(_continued(_rows(21, 30, header=False)))
    assert candidate.drop_rows == 0 and candidate.confirmed


def test_what_is_not_a_candidate():
    other_columns = [["a", "b"], ["1", "2"]]
    assert merge_candidates(_continued(other_columns)) == []
    caption = _block("cap", 2, (50, 40, 550, 55), text="Table 2 Prices")  # a new table's own title
    assert merge_candidates(_continued(_rows(21, 30), between=[caption])) == []
    far = _state([_block("t1", 1, (50, 80, 550, 760), BlockKind.TABLE, rows=_rows(1, 5))], [],
                 [_block("t2", 3, (50, 60, 550, 400), BlockKind.TABLE, rows=_rows(6, 9))])
    assert merge_candidates(far) == []


def test_misaligned_tables_stay_unconfirmed_and_are_reported():
    state = _continued(_rows(21, 30), second_bbox=(200, 60, 590, 400))
    [candidate] = merge_candidates(state)
    assert not candidate.confirmed and propose_merges(state) == []
    assert [u.kind for u in unresolved_items(state) if u.kind != "outline_review"] == ["table_merge_candidate"]


def test_merge_appends_rows_and_keeps_every_source():
    state = _continued(_rows(21, 30))
    changes = CHANGE.validate_python(propose_merges(state))
    outcome = apply_changes(state, changes, actor="program:tables.merge")
    assert outcome.rejected == []
    t1, t2 = (next(b for b in state.blocks if b.id == i) for i in ("t1", "t2"))
    assert t1.cells.n_rows == 31 and t1.cells.slot(30, 0).content == "30" and t2.status == BlockStatus.MERGED
    assert [a.page for a in t1.anchors] == [1, 2]
    assert [(r.kind, r.src, r.dst) for r in state.relations] == [("continues", "t1", "t2")]
    result = check(state)
    assert result.unassigned == [] and result.mismatched == [] and result.accounting.merged == 1
    assert render_markdown(state).count("| --- | --- | --- |") == 1 and [u for u in unresolved_items(state) if u.kind != "outline_review"] == []


def test_merge_legality():
    def rules(state, change):
        return [r.rule for r in check_changes(state, CHANGE.validate_python([{"op": "join", **change}]))]

    state = _continued(_rows(21, 30, header=False))
    assert rules(state, {"first": "t1", "second": "t2", "drop_rows": 1, "reason": "r"}) == ["rows_not_duplicate"]
    assert rules(state, {"first": "t2", "second": "t1", "drop_rows": 0, "reason": "r"}) == ["not_adjacent"]  # experience
    assert rules(state, {"first": "t1", "second": "after", "drop_rows": 0, "reason": "r"}) == ["not_joinable"]
    # §11.5: a table with other columns never continues the one before it, whoever asks
    other_columns = _continued([["a", "b"], ["1", "2"]])
    assert rules(other_columns, {"first": "t1", "second": "t2", "drop_rows": 0, "reason": "r"}) == ["not_merge_candidate"]


def test_a_table_over_three_pages_merges_into_the_first():
    state = _state([_block("t1", 1, (50, 80, 550, 760), BlockKind.TABLE, rows=_rows(1, 20))],
                   [_block("t2", 2, (50, 60, 550, 760), BlockKind.TABLE, rows=_rows(21, 40))],
                   [_block("t3", 3, (50, 60, 550, 300), BlockKind.TABLE, rows=_rows(41, 45))])
    changes = propose_merges(state)
    assert [(c["first"], c["second"]) for c in changes] == [("t1", "t2"), ("t1", "t3")]
    assert apply_changes(state, CHANGE.validate_python(changes), actor="program:tables.merge").rejected == []
    assert next(b for b in state.blocks if b.id == "t1").cells.n_rows == 46


def test_a_table_continued_after_a_page_between_is_joined_only_on_evidence():
    from parserx.ir.evidence import Evidence

    state = _state([_block("t1", 1, (50, 80, 550, 760), BlockKind.TABLE, rows=_rows(1, 20))],
                   [_block("figure-page", 2, (50, 60, 550, 760), text="a full-page drawing between")],
                   [_block("t3", 3, (50, 60, 550, 300), BlockKind.TABLE, rows=_rows(21, 25, header=False))])
    join = {"op": "join", "first": "t1", "second": "t3", "reason": "原件第 3 页写着“续表”", "override": True}
    assert [r.rule for r in check_changes(state, CHANGE.validate_python([{**join, "override": False}]))] == \
        ["not_adjacent"]
    assert [r.rule for r in check_changes(state, CHANGE.validate_python([join]))] == ["override_without_evidence"]
    state.evidence.append(Evidence(id="e-000000000003", how="image", page=3, image="a-3"))
    outcome = apply_changes(state, CHANGE.validate_python([{**join, "evidence": "e-000000000003"}]), actor="agent")
    assert outcome.rejected == [] and next(b for b in state.blocks if b.id == "t1").cells.n_rows == 26


def test_joined_tables_are_separated_again_the_last_first():
    state = _state([_block("t1", 1, (50, 80, 550, 760), BlockKind.TABLE, rows=_rows(1, 20))],
                   [_block("t2", 2, (50, 60, 550, 760), BlockKind.TABLE, rows=_rows(21, 40))],
                   [_block("t3", 3, (50, 60, 550, 300), BlockKind.TABLE, rows=_rows(41, 45))])
    before = {b.id: b.cells for b in state.blocks}
    assert apply_changes(state, CHANGE.validate_python(propose_merges(state)), actor="program:tables.merge").rejected == []
    t1 = next(b for b in state.blocks if b.id == "t1")
    t1.cells = t1.cells.model_copy(update={"cells": [c.model_copy(update={"content": "edited"})
                                                     if (c.row, c.col) == (25, 1) else c for c in t1.cells.cells]})
    unjoin = [{"op": "unjoin", "first": "t1", "second": s, "reason": "两张独立的表"} for s in ("t2", "t3")]
    assert [r.rule for r in check_changes(state, CHANGE.validate_python(unjoin[:1]))] == ["tables_merged"]  # t3 first
    outcome = apply_changes(state, CHANGE.validate_python(unjoin[::-1]), actor="agent")
    assert outcome.rejected == [] and check(state).mismatched == [] and check(state).unassigned == []
    blocks = {b.id: b for b in state.blocks}
    assert blocks["t1"].cells == before["t1"] and blocks["t3"].cells == before["t3"]
    assert blocks["t2"].cells.slot(5, 1).content == "edited"  # the edit stays with its row
    assert all(b.status == BlockStatus.OK for b in blocks.values()) and render_markdown(state).count("| --- | --- | --- |") == 3


def _read_by_the_scan_engine(block):
    from parserx.ir.enums import ObservationStatus, TaskKind
    from parserx.ir.observation import Observation

    obs = Observation(id=f"o-{block.id}", engine="paddleocr", engine_version="1", task=TaskKind.RECOGNIZE,
                      anchor=block.anchors[0], cells=block.cells, status=ObservationStatus.OK)
    block.observations, block.chosen_observation = [obs], obs.id
    return block


def test_a_scanned_table_continues_when_its_left_edge_aligns():
    # the scan engine's table box is as wide as its content: the right edge moves with the text (ocr01)
    def candidate(second_bbox):
        state = _continued(_rows(21, 30, header=False), second_bbox=second_bbox)
        for block in state.blocks:
            if block.kind == BlockKind.TABLE:
                _read_by_the_scan_engine(block)
        return merge_candidates(state)[0]

    narrower = candidate((52, 60, 480, 400))  # right edge 70 pt in: more than 5% of the page
    assert narrower.confirmed and narrower.evidence["edges"] == "left"
    assert not candidate((120, 60, 550, 400)).confirmed  # the left edge moved: another table
    native = merge_candidates(_continued(_rows(21, 30, header=False), second_bbox=(52, 60, 480, 400)))[0]
    assert not native.confirmed and native.evidence["edges"] == "both"  # a ruled table's box is the table


def test_a_merged_cell_cut_by_the_page_break_is_one_again():
    from parserx.tables.merge import merge_grids

    first = TableGrid(n_rows=3, n_cols=2, cells=[Cell(row=0, col=0, content="组"), Cell(row=0, col=1, content="项"),
                                                  Cell(row=1, col=0, rowspan=2, content="ZZ-01"),
                                                  Cell(row=1, col=1, content="a"), Cell(row=2, col=1, content="b")])
    second = TableGrid(n_rows=2, n_cols=2, cells=[Cell(row=0, col=0, rowspan=2, content=""),
                                                   Cell(row=0, col=1, content="c"), Cell(row=1, col=1, content="d")])
    grid = merge_grids(first, second, 0)
    assert grid.slot(4, 0).content == "ZZ-01" and grid.slot(1, 0).rowspan == 4
    lone = second.model_copy(update={"cells": [Cell(row=0, col=0, content="x"), *second.cells[1:]]})
    assert merge_grids(first, lone, 0).slot(3, 0).content == "x"  # a cell of its own stays its own
    repeated = TableGrid(n_rows=3, n_cols=2, cells=[Cell(row=0, col=0, content="组"), Cell(row=0, col=1, content="项"),
                                                     *(c.model_copy(update={"row": c.row + 1}) for c in second.cells)])
    assert merge_grids(first, repeated, 1).slot(4, 0).content == "ZZ-01"  # after the repeated header row
