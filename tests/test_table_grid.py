"""TableGrid (guide §4.3) and SourceAnchor basics."""

import pytest
from pydantic import TypeAdapter, ValidationError

from parserx.ir import AssetAnchor, DocxAnchor, PdfAnchor, SourceAnchor
from parserx.tables import Cell, TableGrid, find_tables


# ── SourceAnchor ────────────────────────────────────────────────────────


def test_anchor_discriminates_on_type():
    adapter = TypeAdapter(SourceAnchor)
    pdf = adapter.validate_python({"type": "pdf", "page": 2, "bbox": [0, 0, 10, 10], "coord_space": "page_pt"})
    docx = adapter.validate_python({"type": "docx", "part": "word/document.xml", "node_path": "/w:body/w:p[3]"})
    asset = adapter.validate_python(
        {"type": "asset", "asset": "a-1", "bbox": [1, 2, 3, 4], "image_size": [100, 50]},
    )
    assert isinstance(pdf, PdfAnchor) and isinstance(docx, DocxAnchor) and isinstance(asset, AssetAnchor)


def test_pixel_anchor_requires_image_size():
    with pytest.raises(ValidationError):
        PdfAnchor(page=1, bbox=(0, 0, 1, 1), coord_space="image_px")


def test_ir_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        DocxAnchor(part="word/document.xml", node_path="/w:body", flag=True)


# ── TableGrid validation ────────────────────────────────────────────────


def test_grid_rejects_overlapping_spans():
    with pytest.raises(ValidationError):
        TableGrid(n_rows=2, n_cols=2, cells=[
            Cell(row=0, col=0, colspan=2, content="a"),
            Cell(row=0, col=1, content="b"),
        ])


def test_grid_rejects_out_of_bounds_cell():
    with pytest.raises(ValidationError):
        TableGrid(n_rows=1, n_cols=1, cells=[Cell(row=0, col=0, rowspan=2, content="a")])


# ── Parsing ─────────────────────────────────────────────────────────────


def test_from_html_keeps_spans_and_headers():
    grid = TableGrid.from_html(
        "<table><tr><th colspan='2'>成绩</th></tr>"
        "<tr><td rowspan='2'>甲</td><td>10</td></tr><tr><td>20</td></tr></table>"
    )
    assert (grid.n_rows, grid.n_cols, grid.header_rows) == (3, 2, 1)
    head = grid.slot(0, 1)
    assert head.content == "成绩" and head.colspan == 2 and head.is_header
    assert grid.slot(2, 0).content == "甲" and grid.slot(2, 0).rowspan == 2
    assert grid.slot(2, 1).content == "20"
    assert grid.has_spans


def test_from_gfm_splits_escaped_pipes_and_br():
    grid = TableGrid.from_gfm([
        "| 名称 | 说明 |",
        "| --- | :---: |",
        r"| a\|b | 第一行<br>第二行 |",
    ])
    assert (grid.n_rows, grid.n_cols, grid.header_rows) == (2, 2, 1)
    assert grid.slot(1, 0).content == "a|b"
    assert grid.slot(1, 1).content == "第一行\n第二行"
    assert not grid.has_spans


def test_gfm_and_html_of_same_table_give_same_grid():
    gfm = TableGrid.from_gfm(["| 甲 | 乙 |", "|---|---|", "| 10 | 20 |"])
    html = TableGrid.from_html("<table><tr><th>甲</th><th>乙</th></tr><tr><td>10</td><td>20</td></tr></table>")
    assert gfm == html


def test_find_tables_locates_gfm_and_html():
    md = (
        "前言\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n正文\n\n"
        "<table><tr><td>x</td></tr></table>\n\n| 不是表格 |\n"
    )
    spans = find_tables(md)
    assert [s.fmt for s in spans] == ["gfm", "html"]
    assert md[spans[0].start:spans[0].end].startswith("| a | b |")
    assert md[spans[1].start:spans[1].end] == "<table><tr><td>x</td></tr></table>"
    assert spans[1].grid.slot(0, 0).content == "x"


def test_find_tables_ignores_code_fences():
    md = "```\n| a | b |\n|---|---|\n```\n"
    assert find_tables(md) == []


# ── Rendering (Phase 1) ─────────────────────────────────────────────────


def _plain_grid() -> TableGrid:
    return TableGrid(n_rows=3, n_cols=2, header_rows=1, cells=[
        Cell(row=0, col=0, content="名称", is_header=True), Cell(row=0, col=1, content="数值", is_header=True),
        Cell(row=1, col=0, content="a|b"), Cell(row=1, col=1, content="10"),
        Cell(row=2, col=0, content="第一行\n第二行"), Cell(row=2, col=1, content=""),
    ])


def _spanned_grid() -> TableGrid:
    return TableGrid(n_rows=3, n_cols=3, header_rows=1, cells=[
        Cell(row=0, col=0, content="项目", is_header=True),
        Cell(row=0, col=1, colspan=2, content="成绩 <A&B>", is_header=True),
        Cell(row=1, col=0, rowspan=2, content="甲"), Cell(row=1, col=1, content="10"), Cell(row=1, col=2, content="11"),
        Cell(row=2, col=1, content="20"), Cell(row=2, col=2, content="21"),
    ])


def test_to_gfm_escapes_and_round_trips():
    grid = _plain_grid()
    gfm = grid.to_gfm()
    assert gfm.splitlines()[:2] == ["| 名称 | 数值 |", "| --- | --- |"]
    assert r"a\|b" in gfm and "第一行<br>第二行" in gfm
    assert TableGrid.from_gfm(gfm.splitlines()) == grid


def test_to_gfm_uses_first_row_as_header_when_none_is_marked():
    grid = TableGrid(n_rows=2, n_cols=1, cells=[Cell(row=0, col=0, content="x"), Cell(row=1, col=0, content="y")])
    assert grid.to_gfm() == "| x |\n| --- |\n| y |"


def test_tables_gfm_cannot_express_go_to_html():
    two_header_rows = _plain_grid().model_copy(update={"header_rows": 2})
    assert not _plain_grid().needs_html
    assert _spanned_grid().needs_html and two_header_rows.needs_html
    for grid in (_spanned_grid(), two_header_rows):
        with pytest.raises(ValueError):
            grid.to_gfm()


def test_to_html_keeps_spans_and_escapes():
    grid = _spanned_grid()
    html = grid.to_html()
    assert '<th colspan="2">成绩 &lt;A&amp;B&gt;</th>' in html
    assert '<td rowspan="2">甲</td>' in html
    assert TableGrid.from_html(html) == grid


def test_to_html_marks_header_rows_even_without_cell_flags():
    grid = TableGrid(n_rows=2, n_cols=1, header_rows=1,
                     cells=[Cell(row=0, col=0, content="h"), Cell(row=1, col=0, content="v")])
    assert TableGrid.from_html(grid.to_html()).header_rows == 1


def test_to_html_fills_uncovered_positions():
    grid = TableGrid(n_rows=1, n_cols=3, cells=[Cell(row=0, col=0, content="a"), Cell(row=0, col=2, content="c")])
    again = TableGrid.from_html(grid.to_html())
    assert again.slot(0, 2).content == "c" and again.slot(0, 1).content == ""


def test_rendered_tables_are_found_in_markdown():
    md = f"前文\n\n{_plain_grid().to_gfm()}\n\n{_spanned_grid().to_html()}\n\n后文\n"
    spans = find_tables(md)
    assert [s.fmt for s in spans] == ["gfm", "html"]
    assert spans[0].grid == _plain_grid() and spans[1].grid == _spanned_grid()
