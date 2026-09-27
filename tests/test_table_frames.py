"""Several tables in one frame (tables T3): cut at full-width rows between parts with other columns, or where a
part starts again under the same kind of head; group and closing rows stay in their table."""

from parserx.accounting import check
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.render import render_markdown
from parserx.tables import Cell, TableGrid
from parserx.tables.frames import frame_parts, split_frames


def _grid(rows):
    """Rows of (text, colspan) on a grid as wide as the widest row."""
    width = max(sum(span for _, span in row) for row in rows)
    cells = []
    for r, row in enumerate(rows):
        col = 0
        for text, span in row:
            cells.append(Cell(row=r, col=col, colspan=span, content=text))
            col += span
    return TableGrid(n_rows=len(rows), n_cols=width, cells=cells)


def _shapes(parts):
    return [f"{p.n_rows}x{p.n_cols}" if isinstance(p, TableGrid) else p for p in parts] if parts else None


def test_parts_with_other_columns_are_tables_of_their_own():
    frame = _grid([[("（一）经费来源", 4)], [("来源", 2), ("金额", 2)], [("财政", 2), ("100", 2)],
                   [("（二）经费支出", 4)], [("科目", 1), ("财政", 1), ("自筹", 1), ("小计", 1)],
                   [("设备费", 1), ("40", 1), ("29.5", 1), ("69.5", 1)]])
    assert _shapes(frame_parts(frame)) == ["（一）经费来源", "2x2", "（二）经费支出", "2x4"]


def test_a_part_starting_again_under_the_same_head_is_a_table_of_its_own():
    frame = _grid([[("(2)测试化验加工费", 2)], [("加工内容", 1), ("与研究任务的相关性", 1)], [("振动测试", 1), ("修正模型", 1)],
                   [("合计：0 万元", 2)], [("(3)燃料动力费", 2)], [("设备名称", 1), ("与研究任务的相关性", 1)],
                   [("压剪试验机", 1), ("性能测试", 1)]])
    assert _shapes(frame_parts(frame)) == ["(2)测试化验加工费", "2x2", "合计：0 万元", "(3)燃料动力费", "2x2"]


def test_group_and_closing_rows_stay_in_their_table():
    grouped = _grid([[("Name", 1), ("Primer", 1)], [("603", 1), ("S603A", 1)], [("Restriction enzyme sites", 2)],
                     [("NheI", 1), ("NheI_fp", 1)]])
    closing = _grid([[("序号", 1), ("名称", 1)], [("1", 1), ("", 1)], [("增值税税率为 %", 2)], [("总价（大写）：", 2)]])
    assert frame_parts(grouped) is None and frame_parts(closing) is None


def test_a_cut_frame_keeps_its_content_and_accounts():
    frame = _grid([[("（一）经费来源", 4)], [("来源", 2), ("金额", 2)], [("财政", 2), ("100", 2)],
                   [("（二）经费支出", 4)], [("科目", 1), ("财政", 1), ("自筹", 1), ("小计", 1)]])
    anchor = PdfAnchor(page=1, bbox=(50, 100, 550, 400), coord_space="page_pt")
    table = Block(id="b-p001-0002", kind=BlockKind.TABLE, order=1, anchors=[anchor], cells=frame)
    after = Block(id="b-p001-0003", kind=BlockKind.TEXT, order=2, anchors=[anchor], text="表后正文")
    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                          blocks=[table, after], ledger=[LedgerEntry(item="i-1", unit="native_line", source=anchor, chars=20,
                                                                      disposition="output", block="b-p001-0002"),
                                                          LedgerEntry(item="i-2", unit="native_line", source=anchor, chars=4,
                                                                      disposition="output", block="b-p001-0003")])
    assert split_frames(state) == ["b-p001-0002"]
    order = [b.id for b in sorted(state.blocks, key=lambda b: b.order)]
    assert order == ["b-p001-0002-f1", "b-p001-0002", "b-p001-0002-f2", "b-p001-0002-f3", "b-p001-0003"]
    result = check(state)
    assert result.mismatched == [] and result.unassigned == []
    markdown = render_markdown(state)
    assert markdown.index("（一）经费来源") < markdown.index("| 来源 | 金额 |") < markdown.index("（二）经费支出") \
        < markdown.index("| 科目 | 财政 | 自筹 | 小计 |") < markdown.index("表后正文")


def test_a_table_read_in_a_drawing_is_its_labels():
    # tables T5: the VLM describes the figure as a diagram; what the scan engine laid out as a table in it is text
    from parserx.ir.relation import Relation
    from parserx.ir.semantic import DiagramSemantic
    from parserx.tables.drawings import tables_in_drawings

    anchor = PdfAnchor(page=1, bbox=(50, 100, 550, 400), coord_space="page_pt")
    figure = Block(id="b-p001-0001", kind=BlockKind.FIGURE, order=0, anchors=[anchor],
                   semantic=DiagramSemantic(diagram_type={"value": "流程图", "level": "visible"}))
    labels = Block(id="b-p001-0001-r001", kind=BlockKind.TABLE, order=1, anchors=[anchor],
                   cells=_grid([[("坡度 0.1", 1), ("系数 9.8", 1)], [("开始", 1), ("", 1)]]))
    photo = Block(id="b-p001-0002-r001", kind=BlockKind.TABLE, order=2, anchors=[anchor],
                  cells=_grid([[("项目", 1), ("数值", 1)], [("甲", 1), ("3", 1)]]))  # read in an undescribed scan
    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                          blocks=[figure, labels, photo],
                          relations=[Relation(id="r1", kind="contains", src="b-p001-0001", dst="b-p001-0001-r001"),
                                     Relation(id="r2", kind="contains", src="b-p001-0002", dst="b-p001-0002-r001")])
    assert tables_in_drawings(state) == ["b-p001-0001-r001"]
    assert (labels.kind, labels.text, labels.cells) == (BlockKind.TEXT, "坡度 0.1 系数 9.8 开始", None)
    assert photo.kind == BlockKind.TABLE
