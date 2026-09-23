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
