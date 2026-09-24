"""Native PDF extraction (guide §6.3, plan P1-4) on synthetic PDFs."""

import io

import fitz
import pytest
from PIL import Image

from parserx.content.order import reading_order
from parserx.content.pdf_native import extract_pdf
from parserx.content.quality import PageSignals, assess_native_layer
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.enums import BlockKind, PageStatus


def _png(w=64, h=48, color=(200, 30, 30)) -> bytes:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 4, 3 * h // 4):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def pdf_path(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # written out of visual order: the extractor must restore top-to-bottom, left-to-right
    page.insert_text((72, 400), "Closing paragraph first line\nand its second line", fontsize=11)
    page.insert_text((72, 100), "Chapter One", fontsize=18, fontname="hebo")
    page.insert_text((320, 200), "Right column", fontsize=11)
    page.insert_text((72, 200), "Left column", fontsize=11)
    page.insert_text((72, 300), "中文段落内容", fontsize=11, fontname="china-s")
    page.insert_image(fitz.Rect(72, 500, 136, 548), stream=_png())
    scanned = doc.new_page(width=595, height=842)
    scanned.insert_image(scanned.rect, stream=_png(300, 420, (90, 90, 90)))
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def test_blocks_follow_visual_order_with_styles(pdf_path):
    ext = extract_pdf(pdf_path)
    page1 = [b for b in ext.blocks if isinstance(b.anchors[0], PdfAnchor) and b.anchors[0].page == 1]
    texts = [b.text for b in page1 if b.kind == BlockKind.TEXT]
    assert texts == ["Chapter One", "Left column Right column", "中文段落内容",
                     "Closing paragraph first line\nand its second line"]
    assert [b.id for b in page1] == [f"b-p001-{i:04d}" for i in range(1, len(page1) + 1)]
    title = page1[0]
    style = title.observations[0].style
    assert style.font_size == pytest.approx(18) and style.bold is True
    assert title.chosen_observation == title.observations[0].id == "o-b-p001-0001-native_pdf-1"
    assert page1[-1].kind == BlockKind.FIGURE  # the image sits below the text


def test_every_line_and_image_is_on_the_ledger(pdf_path):
    ext = extract_pdf(pdf_path)
    lines = [e for e in ext.ledger if e.unit == "native_line"]
    assert len(lines) == 6  # the closing paragraph has two lines
    assert all(e.disposition == "output" and e.block for e in ext.ledger)
    assert sum(e.chars for e in lines) == sum(len("".join(b.text.split())) for b in ext.blocks
                                              if b.kind == BlockKind.TEXT)
    assert [e.item for e in lines][:2] == ["i-p001-00001", "i-p001-00002"]
    images = [e for e in ext.ledger if e.unit == "pdf_image"]
    assert len(images) == 2 and all(e.chars == 0 for e in images)


def test_images_become_assets_linked_from_blocks(pdf_path):
    ext = extract_pdf(pdf_path)
    figure = next(b for b in ext.blocks if b.kind == BlockKind.FIGURE)
    asset_anchor = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
    asset = next(a for a in ext.assets if a.id == asset_anchor.asset)
    assert (asset.width, asset.height) == (64, 48) and asset.role == "original"
    assert asset.source.page == 1 and ext.asset_bytes[asset.path][:4] == b"\x89PNG"


def test_page_without_text_layer_goes_to_the_scan_engine(pdf_path):
    ext = extract_pdf(pdf_path)
    assert [p.status for p in ext.pages] == [PageStatus.DONE, PageStatus.PENDING]
    scan = [b for b in ext.blocks if b.kind == BlockKind.SCAN]
    assert len(scan) == 1 and scan[0].anchors[0].page == 2
    decision = scan[0].decisions[0]
    assert decision.stage == "content_source" and decision.choice == "scan_engine"
    assert decision.reason.startswith("no_text_layer")


def test_extraction_is_deterministic(pdf_path):
    a, b = extract_pdf(pdf_path), extract_pdf(pdf_path)
    assert [x.model_dump() for x in a.blocks] == [x.model_dump() for x in b.blocks]
    assert a.ledger == b.ledger and a.asset_bytes == b.asset_bytes


def test_line_based_table_becomes_a_grid(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    xs, ys = [72, 200, 328], [100, 130, 160]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    for r, row in enumerate([("名称", "数值"), ("甲", "10")]):
        for c, text in enumerate(row):
            page.insert_text((xs[c] + 5, ys[r] + 20), text, fontsize=11, fontname="china-s")
    page.insert_text((72, 300), "表后正文", fontsize=11, fontname="china-s")
    path = tmp_path / "table.pdf"
    doc.save(path)

    ext = extract_pdf(path)
    table = next(b for b in ext.blocks if b.kind == BlockKind.TABLE)
    assert [[c.content for c in row] for row in table.cells.slot_matrix()] == [["名称", "数值"], ["甲", "10"]]
    assert table.text == ""
    cell_lines = [e for e in ext.ledger if e.block == table.id]
    assert len(cell_lines) == 4  # the cells' lines are accounted to the table, not duplicated as text
    assert [b.text for b in ext.blocks if b.kind == BlockKind.TEXT] == ["表后正文"]


# ── Pure helpers ────────────────────────────────────────────────────────


def _signals(**kw):
    base = dict(page_area=500_000.0, text_chars=800, replacement_chars=0, private_use_chars=0,
                image_coverage=0.0, dominant_image_ratio=0.0, chars_in_dominant_image=0, drawings=0)
    base.update(kw)
    return PageSignals(**base)


@pytest.mark.parametrize("signals, reason", [
    (_signals(), "ok"),
    (_signals(text_chars=10, image_coverage=0.9, dominant_image_ratio=0.9), "no_text_layer"),
    (_signals(text_chars=100, drawings=900), "vector_text"),
    (_signals(image_coverage=0.95, dominant_image_ratio=0.95, chars_in_dominant_image=780), "ocr_text_layer"),
    (_signals(replacement_chars=60), "garbled"),
    (_signals(private_use_chars=60), "garbled"),
    (_signals(invisible_chars=700), "ocr_text_layer"),
    (_signals(text_chars=0, image_coverage=0.45, dominant_image_ratio=0.45), "image_content"),
    (_signals(text_chars=150, image_coverage=0.35, dominant_image_ratio=0.2), "image_content"),
    (_signals(text_chars=150, image_coverage=0.25, dominant_image_ratio=0.2), "ok"),  # a figure with a caption
])
def test_native_layer_verdicts(signals, reason):
    verdict = assess_native_layer(signals)
    assert verdict.reason == reason and verdict.ok == (reason == "ok")


def test_reading_order_groups_rows_left_to_right():
    boxes = [(300, 100, 400, 112), (72, 101, 200, 113), (72, 50, 500, 70), (72, 200, 500, 260)]
    assert reading_order(boxes) == [2, 1, 0, 3]


def _column_lines(x0, x1, top, n, leading=16, height=11):
    return [(x0, top + leading * i, x1, top + leading * i + height) for i in range(n)]


def test_two_columns_of_lines_are_read_column_by_column():
    title = [(57, 40, 538, 60)]
    left, right = _column_lines(57, 292, 80, 12), _column_lines(306, 538, 80, 12)  # same leading: rows align
    page_number = [(296, 780, 302, 790)]  # centred in the gutter
    boxes = title + right + left + page_number
    order = reading_order(boxes)
    ids = {i: ("title" if i == 0 else "right" if i <= 12 else "left" if i <= 24 else "number") for i in range(26)}
    assert [ids[i] for i in order] == ["title"] + ["left"] * 12 + ["right"] * 12 + ["number"]
    assert order[1:13] == sorted(order[1:13])  # top to bottom inside the column


def test_full_width_blocks_start_a_new_column_section():
    boxes = [(57, 80, 292, 190), (57, 200, 292, 300), (306, 80, 538, 150), (306, 160, 538, 300),  # section 1
             (57, 320, 538, 500),                                                                 # full-width figure
             (57, 520, 292, 700), (306, 520, 538, 640)]                                           # section 2
    assert reading_order(boxes) == [0, 1, 2, 3, 4, 5, 6]
    perm = (5, 3, 6, 1, 4, 0, 2)
    assert [perm[i] for i in reading_order([boxes[i] for i in perm])] == [0, 1, 2, 3, 4, 5, 6]


def test_forms_and_unruled_tables_keep_row_order():
    labels = ["Name", "Order date", "Billing address", "Total"]
    form = [box for i, label in enumerate(labels)
            for box in ((57, 100 + 20 * i, 57 + 9 * len(label), 112 + 20 * i), (300, 100 + 20 * i, 420, 112 + 20 * i))]
    assert reading_order(form) == list(range(len(form)))
    table = [box for i in range(5) for box in ((57, 100 + 18 * i, 57 + 30 + 12 * (i % 3), 112 + 18 * i),
                                             (250, 100 + 18 * i, 300, 112 + 18 * i),
                                             (450, 100 + 18 * i, 500, 112 + 18 * i))]
    assert reading_order(table) == list(range(len(table)))


def test_single_column_with_short_lines_reads_top_to_bottom():
    boxes = [(57, 100, 538, 112), (57, 116, 200, 128), (57, 140, 538, 152), (400, 156, 538, 168), (57, 172, 300, 184)]
    assert reading_order(boxes) == [0, 1, 2, 3, 4]


def test_two_column_page_extracts_in_column_order(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for x, side in ((57, "Left"), (306, "Right")):  # typeset column by column, as layout programs write them
        for i in range(20):
            page.insert_text((x, 90 + 16 * i), f"{side} column line {i:02d} with enough words", fontsize=10)
    path = tmp_path / "cols.pdf"
    doc.save(path)
    text = "\n".join(b.text for b in extract_pdf(path).blocks)
    assert text.index("Left column line 19") < text.index("Right column line 00")


def test_invisible_text_over_tiled_images_is_an_ocr_layer(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(0, 0, 595, 400), stream=_png(200, 134))  # a scan split into two tiles
    page.insert_image(fitz.Rect(0, 400, 595, 842), stream=_png(200, 150, (10, 10, 10)))
    page.insert_text((72, 100), "中华人民共和压业标准" * 3, fontsize=11, fontname="china-s", render_mode=3)
    path = tmp_path / "searchable-scan.pdf"
    doc.save(path)

    ext = extract_pdf(path)
    assert ext.pages[0].status == PageStatus.PENDING
    assert sorted(b.kind for b in ext.blocks) == [BlockKind.SCAN, BlockKind.SCAN, BlockKind.TEXT]
    assert ext.blocks[0].decisions[0].reason.startswith("ocr_text_layer")
