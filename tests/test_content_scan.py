"""Scan engine output → Observations and Blocks (guide §6.4, plan P1-4), from synthetic parsing_res_list."""

import io

import fitz
from PIL import Image

from parserx.content.scan import PageScan, batch_pdf, page_blocks, scan_order
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.enums import BlockKind, BlockStatus


def _entry(label, content, bbox, order):
    return {"block_label": label, "block_content": content, "block_bbox": bbox, "block_order": order}


_TABLE = "<table><tr><td>钢筋种类</td><td>强度</td></tr><tr><td>HPB300</td><td>250</td></tr></table>"


def _scan(entries, width=1000, height=1400) -> PageScan:
    return PageScan(page=2, raw={"prunedResult": {"width": width, "height": height, "parsing_res_list": entries}},
                    raw_ref="k" * 64, engine_version="PaddleOCR-VL-1.6")


ENTRIES = [
    _entry("header", "前言", [800, 40, 900, 60], None),
    _entry("paragraph_title", "3.2 材料", [100, 100, 400, 140], 1),
    _entry("text", "正文第一段。", [100, 160, 900, 220], 2),
    _entry("figure_title", "表3.2 强度设计值", [300, 240, 700, 270], None),
    _entry("table", _TABLE, [100, 280, 900, 500], None),
    _entry("text", "表后说明。", [100, 520, 900, 560], 3),
    _entry("image", "图中文字", [100, 600, 500, 900], None),
    _entry("number", "10", [480, 1350, 520, 1380], None),
]


def test_unordered_regions_are_placed_by_position():
    boxes = [e["block_bbox"] for e in ENTRIES]
    orders = [e["block_order"] for e in ENTRIES]
    assert scan_order(boxes, orders) == [0, 1, 2, 3, 4, 5, 6, 7]
    # ordered flow keeps the engine's order even when it is not top-down
    assert scan_order([[0, 100, 1, 110], [0, 50, 1, 60]], [1, 2]) == [0, 1]


def test_unordered_regions_follow_their_own_column():
    boxes = [[100, 100, 500, 300], [100, 450, 500, 900],  # left column, engine order 1, 2
             [600, 100, 900, 200], [600, 220, 900, 300],  # right column, engine order 3, 4
             [600, 330, 900, 360], [600, 370, 900, 600],  # a caption and a table in the right column, unordered
             [600, 650, 900, 700]]                        # right column, engine order 5
    assert scan_order(boxes, [1, 2, 3, 4, None, None, 5]) == [0, 1, 2, 3, 4, 5, 6]


def test_a_caption_moves_with_its_full_width_table():
    boxes = [[100, 100, 500, 900], [600, 100, 900, 950],  # two columns, engine order 1, 2
             [100, 970, 200, 990], [100, 995, 900, 1300]]  # a short caption over a full-width table, unordered
    assert scan_order(boxes, [1, 2, None, None]) == [0, 1, 2, 3]


def test_blocks_ids_kinds_and_coordinates():
    result = page_blocks(_scan(ENTRIES), page_size=(500.0, 700.0), first_seq=4, first_item=7)
    blocks = result.blocks
    assert [b.id for b in blocks] == [f"b-p002-{i:04d}" for i in range(4, 12)]
    assert [b.kind for b in blocks] == [
        BlockKind.HEADER, BlockKind.TITLE, BlockKind.TEXT, BlockKind.CAPTION, BlockKind.TABLE,
        BlockKind.TEXT, BlockKind.FIGURE, BlockKind.PAGE_NUMBER]
    obs = blocks[1].observations[0]
    assert obs.engine == "paddleocr" and obs.label == "paragraph_title" and obs.raw_ref == "k" * 64
    assert obs.anchor.coord_space == "image_px" and obs.anchor.image_size == (1000, 1400)
    assert obs.anchor.transform == (0.5, 0.0, 0.0, 0.5, 0.0, 0.0)
    page_anchor = blocks[1].anchors[0]
    assert isinstance(page_anchor, PdfAnchor) and page_anchor.bbox == (50.0, 50.0, 200.0, 70.0)
    assert all(b.decisions[0].stage == "content_source" and b.decisions[0].choice == "scan_engine" for b in blocks)


def test_tables_become_grids_and_furniture_is_excluded():
    result = page_blocks(_scan(ENTRIES), page_size=(500.0, 700.0), first_seq=1, first_item=1)
    table = next(b for b in result.blocks if b.kind == BlockKind.TABLE)
    assert table.cells.slot(1, 1).content == "250" and table.text == ""
    furniture = [b for b in result.blocks if b.kind in (BlockKind.HEADER, BlockKind.PAGE_NUMBER)]
    assert all(b.status == BlockStatus.EXCLUDED and b.decisions[-1].stage == "exclude" for b in furniture)
    ledger = {e.block: e for e in result.ledger}
    assert [e.item for e in result.ledger] == [f"i-p002-{i:05d}" for i in range(1, 9)]
    assert ledger[furniture[0].id].disposition == "excluded"
    assert ledger[table.id].chars == len("钢筋种类强度HPB300250")


def test_figures_are_cropped_from_the_page_render():
    img = Image.new("RGB", (1000, 1400), "white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    result = page_blocks(_scan(ENTRIES), page_size=(500.0, 700.0), first_seq=1, first_item=1,
                         page_image=(buf.getvalue(), 1000, 1400, 144.0))
    figure = next(b for b in result.blocks if b.kind == BlockKind.FIGURE)
    crop_anchor = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
    crop = next(a for a in result.assets if a.id == crop_anchor.asset)
    render = next(a for a in result.assets if a.role == "render")
    assert crop.role == "crop" and crop.derived_from == render.id and (crop.width, crop.height) == (400, 300)
    assert crop.transform == (1.0, 0.0, 0.0, 1.0, 100.0, 600.0)
    assert figure.text == "" and figure.observations[0].text == "图中文字"  # text inside the image is evidence


def test_unknown_labels_keep_their_content():
    result = page_blocks(_scan([_entry("sidebar_note", "旁注", [0, 0, 10, 10], 1)]), page_size=(5.0, 7.0),
                         first_seq=1, first_item=1)
    assert result.blocks[0].kind == BlockKind.OTHER and result.blocks[0].text == "旁注"
    assert "sidebar_note" in result.warnings[0]


def test_malformed_table_html_is_kept_as_text():
    broken = "<table><tr><td>a</td><td rowspan='2'>b</td></tr><tr><td colspan='2'>c</td></tr></table>"  # c overlaps b
    result = page_blocks(_scan([_entry("table", broken, [0, 0, 10, 10], None)]), page_size=(5.0, 7.0),
                         first_seq=1, first_item=1)
    block = result.blocks[0]  # tables hold content only in cells; the raw HTML stays readable as text
    assert block.kind == BlockKind.OTHER and block.status == BlockStatus.DEGRADED and block.text == broken
    assert "not convertible" in result.warnings[0]


def test_batch_pdf_is_byte_stable(tmp_path):
    doc = fitz.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"page {i + 1}")
    path = tmp_path / "src.pdf"
    doc.save(path)
    with fitz.open(path) as src:
        a, b = batch_pdf(src, [1, 3]), batch_pdf(src, [1, 3])
    assert a == b
    with fitz.open(stream=a, filetype="pdf") as sub:
        assert [p.get_text().strip() for p in sub] == ["page 1", "page 3"]


def test_engine_line_breaks_outside_formulas_become_real_line_breaks():
    # The engine writes an in-cell line break as a literal backslash-n; inside $…$ it is LaTeX (\nu).
    table = r"<table><tr><td>种类</td><td>值</td></tr><tr><td>HRB400、HRB500\nHRBF400</td><td>$\nu_c$</td></tr></table>"
    entries = [_entry("table", table, [100, 100, 900, 400], 1),
               _entry("text", r"泊松比 $\nu_{c}$ 可采用0.2。\n下一行", [100, 420, 900, 480], 2)]
    result = page_blocks(_scan(entries), page_size=(500.0, 700.0), first_seq=1, first_item=1)
    grid = next(b for b in result.blocks if b.kind == BlockKind.TABLE).cells
    assert grid.slot(1, 0).content == "HRB400、HRB500\nHRBF400" and grid.slot(1, 1).content == r"$\nu_c$"
    assert "HRB400、HRB500<br>HRBF400" in grid.to_html()
    text = next(b for b in result.blocks if b.kind == BlockKind.TEXT).text
    assert text == "泊松比 $\\nu_{c}$ 可采用0.2。\n下一行"
