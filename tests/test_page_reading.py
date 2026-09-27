"""Two-way comparison of the output with a local reading of each page (guide §9.5, Q56), on synthetic states."""

import pytest

from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, PageReading, PageState, ReadLine
from parserx.tables.grid import Cell, TableGrid
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import unresolved_items
from parserx.reading.compare import unaccounted_lines, unseen_segments


def _block(bid, page, bbox, text="", *, kind=BlockKind.TEXT, status=BlockStatus.OK, cells=None, pages=None):
    anchors = [PdfAnchor(page=p, bbox=b, coord_space="page_pt") for p, b in (pages or [(page, bbox)])]
    grid = None
    if cells is not None:
        grid = TableGrid(n_rows=len(cells), n_cols=len(cells[0]),
                         cells=[Cell(row=r, col=c, content=v) for r, row in enumerate(cells) for c, v in enumerate(row)])
    return Block(id=bid, kind=kind, order=0, status=status, anchors=anchors, text=text, cells=grid)


def _line(text, bbox, score=0.99):
    return ReadLine(bbox=bbox, text=text, score=score)


def _state(blocks, readings, pages=1):
    return DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=n, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842)) for n in range(1, pages + 1)],
        blocks=blocks, readings=readings)


_TABLE_BOX = (100, 100, 500, 300)


def _form(lines, **kw):
    table = _block("b-t", 1, _TABLE_BOX, kind=BlockKind.TABLE,
                   cells=[["1、核心技术"], ["（1）本项目在橡胶中加入高分子改性材料，将极大提高支座的承载能力。"]], **kw)
    return _state([table], [PageReading(n=1, engine="local", dpi=150, lines=lines)])


def test_a_visible_line_no_block_accounts_for_is_listed():
    dropped = _line("（2）采用橡胶与钢板硫化成一体。", (110, 260, 300, 275))
    state = _form([_line("1、核心技术", (110, 110, 200, 125)),
                   _line("（1）本项目在橡胶中加入高分子改性材料，将极大提高支座的承载能力。", (110, 230, 480, 245)),
                   dropped])
    assert unaccounted_lines(state) == {1: [dropped]}


def test_a_local_misreading_is_within_tolerance():
    state = _form([_line("（1）本項目在橡膠中加入高分子改性材料，將极大提高支座的承載能力。", (110, 230, 480, 245))])
    assert unaccounted_lines(state) == {}


def test_text_inside_figures_and_formula_regions_is_not_compared():
    figure = _block("b-f", 1, (100, 400, 300, 600), kind=BlockKind.FIGURE)
    reading = PageReading(n=1, engine="local", dpi=150, not_prose=[(320, 400, 500, 450)], lines=[
        _line("Accuracy (%)", (120, 420, 200, 432)),  # inside the figure block
        _line("E = mc2 (3)", (330, 410, 480, 430)),  # inside a region the detector calls a formula
    ])
    assert unaccounted_lines(_state([figure], [reading])) == {}


def test_a_line_unaccounted_on_several_pages_is_furniture():
    note = "©1994-2014 China Academic Journal Electronic Publishing House"
    readings = [PageReading(n=n, engine="local", dpi=150, lines=[_line(note, (100, 800, 500, 812))]) for n in (1, 2)]
    readings[1].lines.append(_line("只在这一页出现的一行正文", (100, 400, 300, 412)))
    found = unaccounted_lines(_state([], readings, pages=2))
    assert [ln.text for lines in found.values() for ln in lines] == ["只在这一页出现的一行正文"]


def test_text_found_elsewhere_on_the_page_is_accounted():
    a = _block("b-1", 1, (100, 100, 300, 120), "第一段")
    b = _block("b-2", 1, (100, 500, 300, 520), "页面其他位置输出的一行")
    reading = PageReading(n=1, engine="local", dpi=150, lines=[_line("页面其他位置输出的一行", (100, 101, 300, 119))])
    assert unaccounted_lines(_state([a, b], [reading])) == {}


def test_a_superseded_reading_is_no_destination():
    duplicate = _block("b-n", 1, _TABLE_BOX, "（2）采用橡胶与钢板硫化成一体。", status=BlockStatus.DUPLICATE)
    state = _form([_line("（2）采用橡胶与钢板硫化成一体。", (110, 260, 300, 275))])
    state.blocks.append(duplicate)
    assert list(unaccounted_lines(state)) == [1]
    excluded = _block("b-x", 1, _TABLE_BOX, "（2）采用橡胶与钢板硫化成一体。", kind=BlockKind.FOOTER,
                      status=BlockStatus.EXCLUDED)  # a decision with a reason is a destination
    state.blocks[-1] = excluded
    assert unaccounted_lines(state) == {}


def test_only_a_shown_figure_holds_the_text_inside_it():
    # §11.5: an image that is not shown is no destination for the text on it (a hidden strip or background
    # would otherwise hide every line it covers from the comparison)
    state = _form([_line("3901", (110, 260, 140, 275))])
    figure = _block("b-f", 1, (100, 250, 150, 300), kind=BlockKind.FIGURE)
    state.blocks.append(figure)
    assert unaccounted_lines(state) == {}  # shown: its description or transcription is the text's destination
    figure.status = BlockStatus.EXCLUDED
    assert list(unaccounted_lines(state)) == [1]


def test_output_text_not_seen_on_the_page_is_listed():
    block = _block("b-1", 1, (100, 100, 500, 140), "近年来桥梁隔震技术得到了较快发展。\n扫描全能王 创建")
    reading = PageReading(n=1, engine="local", dpi=150, lines=[_line("近年来桥梁隔震技术得到了较快发展。", (100, 100, 500, 118)),
                                                             _line("闷王王", (480, 120, 500, 140), score=0.6)])
    assert unseen_segments(_state([block], [reading])) == {"b-1": ["扫描全能王 创建"]}


def test_a_table_is_compared_with_every_page_it_spans():
    table = _block("b-t", 1, None, kind=BlockKind.TABLE, cells=[["设备名称"], ["高性能工作站"]],
                   pages=[(1, (100, 700, 500, 800)), (2, (100, 50, 500, 100))])
    readings = [PageReading(n=1, engine="local", dpi=150, lines=[_line("设备名称", (110, 710, 200, 722))]),
                PageReading(n=2, engine="local", dpi=150, lines=[_line("高性能工作站", (110, 60, 220, 72))])]
    assert unseen_segments(_state([table], readings, pages=2)) == {}


def test_the_worklist_lists_both_directions():
    block = _block("b-1", 1, (100, 100, 500, 140), "近年来桥梁隔震技术得到了较快发展。\n扫描全能王 创建")
    reading = PageReading(n=1, engine="local", dpi=150, lines=[
        _line("近年来桥梁隔震技术得到了较快发展。", (100, 100, 500, 118)),
        _line("（2）采用橡胶与钢板硫化成一体。", (100, 300, 300, 315))])
    items = {(u.kind, u.target): [q.doc_text for q in u.quotes] for u in unresolved_items(_state([block], [reading]))}
    assert items[(UnresolvedKind.TEXT_UNACCOUNTED, "p1")] == ["（2）采用橡胶与钢板硫化成一体。"]
    assert items[(UnresolvedKind.TEXT_NOT_SEEN, "b-1")] == ["扫描全能王 创建"]


def test_lines_inside_a_table_are_listed_on_the_table():
    dropped = _line("（2）采用橡胶与钢板硫化成一体。", (110, 260, 300, 275))
    outside = _line("三、项目的核心技术与创新点", (110, 400, 300, 415))
    items = {(u.kind, u.target): u for u in unresolved_items(_form([dropped, outside]))}
    on_table = items[(UnresolvedKind.TEXT_UNACCOUNTED, "b-t")]
    assert [q.doc_text for q in on_table.quotes] == [dropped.text] and "none of its cells" in on_table.detail
    assert [q.doc_text for q in items[(UnresolvedKind.TEXT_UNACCOUNTED, "p1")].quotes] == [outside.text]


def test_tables_cut_from_one_frame_list_a_line_once():
    # Q93: the parts of one frame share its region; the line goes to the first, the item names the others
    first = _block("b-t", 1, _TABLE_BOX, kind=BlockKind.TABLE, cells=[["设备名称", "数量"]])
    second = _block("b-t-f1", 1, _TABLE_BOX, kind=BlockKind.TABLE, cells=[["合计", "3"]])
    second.order = 1
    reading = PageReading(n=1, engine="local", dpi=150, lines=[_line("与研究任务的相关性", (300, 110, 480, 125))])
    items = [u for u in unresolved_items(_state([second, first], [reading])) if u.kind == UnresolvedKind.TEXT_UNACCOUNTED]
    assert [u.target for u in items] == ["b-t"] and "b-t-f1" in items[0].detail


def test_nothing_to_compare_without_a_reading():
    block = _block("b-1", 1, (100, 100, 500, 140), "正文")
    assert unaccounted_lines(_state([block], [])) == {} and unseen_segments(_state([block], [])) == {}
    empty = PageReading(n=1, engine="local", dpi=150)  # the reader saw nothing: no evidence either way
    assert unaccounted_lines(_state([block], [empty])) == {} and unseen_segments(_state([block], [empty])) == {}


# ── The process step ────────────────────────────────────────────────────


def test_process_reads_every_page_and_lists_what_only_the_image_shows(tmp_path):
    import pymupdf

    from parserx.tools import call_tool, workspace_init
    from tests.test_tools_contract import FakeReader, _config, _context

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "A line in the text layer of page one.", fontsize=11)
    turned = doc.new_page(width=595, height=842)
    turned.insert_text((72, 100), "Page two is shown turned by ninety degrees.", fontsize=11)
    turned.set_rotation(90)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    reader = FakeReader({  # call n reads page n; boxes in pixels of the 150 dpi render
        1: [((150.0, 185.0, 700.0, 215.0), "A line in the text layer of page one.", 0.99),
            ((150.0, 600.0, 700.0, 630.0), "A printed line the text layer lacks", 0.98)],
        2: [((10.0, 10.0, 40.0, 60.0), "x", 0.9)],
    })
    base = _context()

    class Context(base):
        def _new_reader(self):
            return reader

    config = _config()
    workspace_init(path, tmp_path / "ws", config=config)
    envelope, _ = call_tool("run_pipeline", tmp_path / "ws", {}, config=config, context_factory=lambda ws, c: Context(ws, c))
    assert "reading" in [s.step for s in envelope.result.steps] and reader.calls == 2
    from parserx.workspace import Workspace

    state = Workspace.open(tmp_path / "ws").load()
    assert [r.n for r in state.readings] == [1, 2] and state.engines["reading"] == "fake-reader-1"
    assert state.readings[0].lines[1].bbox == (72.0, 288.0, 336.0, 302.4)  # pixels at 150 dpi → points
    x0, y0, x1, y1 = state.readings[1].lines[0].bbox  # the turned page: back in unrotated page space
    assert x1 - x0 == pytest.approx(24.0) and y1 - y0 == pytest.approx(14.4)
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.TEXT_UNACCOUNTED]
    assert [u.target for u in items] == ["p1"]
    assert [q.doc_text for q in items[0].quotes] == ["A printed line the text layer lacks"]
    again, _ = call_tool("run_pipeline", tmp_path / "ws", {}, config=config, context_factory=lambda ws, c: Context(ws, c))
    assert reader.calls == 2  # read once
