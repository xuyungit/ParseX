"""Native PDF extraction (guide §6.3, plan P1-4) on synthetic PDFs."""

import io

import pymupdf
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
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # written out of visual order: the extractor must restore top-to-bottom, left-to-right
    page.insert_text((72, 400), "Closing paragraph first line\nand its second line", fontsize=11)
    page.insert_text((72, 100), "Chapter One", fontsize=18, fontname="hebo")
    page.insert_text((320, 200), "Right column", fontsize=11)
    page.insert_text((72, 200), "Left column", fontsize=11)
    page.insert_text((72, 300), "中文段落内容", fontsize=11, fontname="china-s")
    page.insert_image(pymupdf.Rect(72, 500, 136, 548), stream=_png())
    scanned = doc.new_page(width=595, height=842)
    scanned.insert_image(scanned.rect, stream=_png(300, 420, (90, 90, 90)))
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def test_blocks_follow_visual_order_with_styles(pdf_path):
    ext = extract_pdf(pdf_path)
    page1 = [b for b in ext.blocks if isinstance(b.anchors[0], PdfAnchor) and b.anchors[0].page == 1]
    texts = [b.text for b in page1 if b.kind == BlockKind.TEXT]
    assert texts == ["Chapter One", "Left column", "Right column", "中文段落内容",  # a column gap is not one line
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
    doc = pymupdf.open()
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
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for x, side in ((57, "Left"), (306, "Right")):  # typeset column by column, as layout programs write them
        for i in range(20):
            page.insert_text((x, 90 + 16 * i), f"{side} column line {i:02d} with enough words", fontsize=10)
    path = tmp_path / "cols.pdf"
    doc.save(path)
    text = "\n".join(b.text for b in extract_pdf(path).blocks)
    assert text.index("Left column line 19") < text.index("Right column line 00")


def test_invisible_text_over_tiled_images_is_an_ocr_layer(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 400), stream=_png(200, 134))  # a scan split into two tiles
    page.insert_image(pymupdf.Rect(0, 400, 595, 842), stream=_png(200, 150, (10, 10, 10)))
    page.insert_text((72, 100), "中华人民共和压业标准" * 3, fontsize=11, fontname="china-s", render_mode=3)
    path = tmp_path / "searchable-scan.pdf"
    doc.save(path)

    ext = extract_pdf(path)
    assert ext.pages[0].status == PageStatus.PENDING
    assert sorted(b.kind for b in ext.blocks) == [BlockKind.SCAN, BlockKind.SCAN, BlockKind.TEXT]
    assert ext.blocks[0].decisions[0].reason.startswith("ocr_text_layer")


def _paged_pdf(tmp_path, pages, header=None, footer=None, name="paged.pdf"):
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        page = doc.new_page(width=595, height=842)
        if header:
            page.insert_text((57, 40), header(n), fontsize=9)
        for i in range(5):
            page.insert_text((57, 120 + 16 * i), f"Body text of page {n}, line {i}, with enough words.", fontsize=10)
        if footer:
            page.insert_text((290, 815), footer(n), fontsize=9)
    path = tmp_path / name
    doc.save(path)
    return path


def test_running_headers_and_page_numbers_are_excluded(tmp_path):
    path = _paged_pdf(tmp_path, 3, header=lambda n: "Annual Report 2025 | Example Company",
                      footer=lambda n: f"- {n} -")
    ext = extract_pdf(path)
    by_text = {b.text: b for b in ext.blocks}
    header, number = by_text["Annual Report 2025 | Example Company"], by_text["- 2 -"]
    assert (header.kind, header.status) == (BlockKind.HEADER, "excluded")
    assert (number.kind, number.status) == (BlockKind.PAGE_NUMBER, "excluded")
    assert number.decisions[-1].stage == "exclude" and number.decisions[-1].evidence["pages"] == 3
    body = [b for b in ext.blocks if b.text.startswith("Body text")]
    assert body and all(b.kind == BlockKind.TEXT and b.status == "ok" for b in body)
    furniture = {b.id for b in ext.blocks if b.status == "excluded"}
    assert all(e.disposition == "excluded" for e in ext.ledger if e.block in furniture)
    assert len(furniture) == 6


def test_margin_text_that_does_not_repeat_stays(tmp_path):
    single = _paged_pdf(tmp_path, 1, footer=lambda n: "7", name="single.pdf")
    assert all(b.status == "ok" for b in extract_pdf(single).blocks)
    varying = _paged_pdf(tmp_path, 3, footer=lambda n: ["Contact us", "See the appendix", "End of report"][n - 1],
                         name="varying.pdf")
    assert all(b.status == "ok" for b in extract_pdf(varying).blocks)


# ── Text in another direction: stamps and watermarks (Phase 3 D1) ───────

_ROWS = [("名称", "数量", "说明"), ("甲", "8", "第一项"), ("乙", "12", "第二项")]


def _stamped_pdf(tmp_path, pages, stamp=None, cell_label=None, name="stamped.pdf"):
    """A ruled 3×3 table per page; *stamp* is drawn at 45° across it, *cell_label* upright-rotated inside one cell."""
    doc = pymupdf.open()
    xs, ys = [72, 232, 392, 522], [100, 160, 220, 280]
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        for r, row in enumerate(_ROWS):
            for c, text in enumerate(row):
                page.insert_text((xs[c] + 8, ys[r] + 34), text, fontsize=11, fontname="china-s")
        if cell_label:
            page.insert_text((200, 150), cell_label, fontsize=11, fontname="china-s", rotate=90)
        page.insert_text((72, 400), "表后正文", fontsize=11, fontname="china-s")
        if stamp:
            pivot = pymupdf.Point(100, 330)
            page.insert_text(pivot, stamp, fontsize=40, morph=(pivot, pymupdf.Matrix(45)))
    path = tmp_path / name
    doc.save(path)
    return path


def _cells(block) -> list[list[str]]:
    return [[c.content for c in row] for row in block.cells.slot_matrix()]


def test_a_stamp_across_a_table_is_not_cell_text(tmp_path):
    ext = extract_pdf(_stamped_pdf(tmp_path, 1, stamp="2024-YF09-00061-SN"))
    table = next(b for b in ext.blocks if b.kind == BlockKind.TABLE)
    assert _cells(table) == [list(r) for r in _ROWS]  # none of the stamp's characters in a cell
    stamp = [b for b in ext.blocks if b.text.replace(" ", "") == "2024-YF09-00061-SN"]
    assert len(stamp) == 1 and stamp[0].status == "ok"  # one page: no evidence it is furniture, so it stays
    stamp_line = next(e for e in ext.ledger if e.chars == len("2024-YF09-00061-SN"))
    assert stamp_line.block == stamp[0].id  # accounted to its own block, not to the table


def test_a_stamp_repeated_on_pages_is_excluded_as_a_watermark(tmp_path):
    ext = extract_pdf(_stamped_pdf(tmp_path, 3, stamp="2024-YF09-00061-SN"))
    tables = [b for b in ext.blocks if b.kind == BlockKind.TABLE]
    assert len(tables) == 3 and all(_cells(t) == [list(r) for r in _ROWS] for t in tables)
    marks = [b for b in ext.blocks if b.kind == BlockKind.WATERMARK]
    assert len(marks) == 3 and all(b.status == "excluded" for b in marks)
    assert marks[0].decisions[-1].stage == "exclude" and marks[0].decisions[-1].evidence["pages"] == 3
    assert all(e.disposition == "excluded" for e in ext.ledger if e.block in {b.id for b in marks})
    assert all(b.status == "ok" for b in ext.blocks if b.text == "表后正文")


def test_rotated_text_inside_one_cell_stays_in_the_cell(tmp_path):
    ext = extract_pdf(_stamped_pdf(tmp_path, 2, cell_label="合计"))
    tables = [b for b in ext.blocks if b.kind == BlockKind.TABLE]
    assert all("合计" in _cells(t)[0][0] for t in tables)  # a vertical label inside one cell is that cell's text
    assert not any(b.kind == BlockKind.WATERMARK for b in ext.blocks)


# ── Table grids: rows without rules, drawings, overlapping cells (Phase 3 D3, D6) ──


def _ruled(page, xs, ys):
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))


def test_a_row_of_aligned_items_without_rules_is_split_into_rows(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _ruled(page, [72, 250, 480], [100, 116, 200])  # rules only above, below the header and at the bottom
    page.insert_text((78, 111), "Category", fontsize=9)
    page.insert_text((256, 111), "Examples", fontsize=9)
    items = [("Array operations", "Concat, Slice, Split"), ("Matrix operations", "MatMul, MatrixInverse"),
             ("Stateful operations", "Variable, Assign"), ("Checkpointing", "Save, Restore")]
    for i, (a, b) in enumerate(items):
        page.insert_text((78, 130 + 16 * i), a, fontsize=9)
        page.insert_text((256, 130 + 16 * i), b, fontsize=9)
    path = tmp_path / "unruled.pdf"
    doc.save(path)
    table = next(b for b in extract_pdf(path).blocks if b.kind == BlockKind.TABLE)
    assert _cells(table) == [["Category", "Examples"], *[list(item) for item in items]]


def test_a_row_whose_cells_just_wrap_stays_one_row(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _ruled(page, [72, 250, 480], [100, 116, 200])
    page.insert_text((78, 111), "Name", fontsize=9)
    page.insert_text((256, 111), "Description", fontsize=9)
    for x0, x1, text in ((72, 250, "Element-wise mathematical operations on every value"),
                         (250, 480, "Adds, subtracts, multiplies and divides arrays of the same shape elementwise")):
        words, line, lines = text.split(), "", []
        for word in words:  # fill each line to the cell's width, as a wrapping paragraph does
            trial = f"{line} {word}".strip()
            if pymupdf.get_text_length(trial, fontsize=9) > x1 - x0 - 12 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        assert len(lines) == 2
        for i, part in enumerate(lines):
            page.insert_text((x0 + 6, 130 + 12 * i), part, fontsize=9)
    path = tmp_path / "wrapped.pdf"
    doc.save(path)
    table = next(b for b in extract_pdf(path).blocks if b.kind == BlockKind.TABLE)
    assert len(_cells(table)) == 2


def test_a_ruled_grid_without_text_is_no_table(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _ruled(page, [100, 150, 200, 250], [100, 130])  # boxes of a diagram
    page.insert_text((72, 300), "Figure 8: Model parallel training", fontsize=9)
    path = tmp_path / "boxes.pdf"
    doc.save(path)
    ext = extract_pdf(path)
    assert not any(b.kind == BlockKind.TABLE for b in ext.blocks)


def test_a_grid_the_layout_detector_does_not_see_is_read_as_text(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    _ruled(page, [72, 400, 520], [100, 300])  # a frame around body text and a margin note
    page.insert_text((80, 120), "1. Deployment", fontsize=11)
    page.insert_text((80, 140), "The inventory file lists every group.", fontsize=10)
    page.insert_text((405, 120), "Comment: how is it checked?", fontsize=7)
    path = tmp_path / "framed.pdf"
    doc.save(path)
    seen_nothing = extract_pdf(path, layout=lambda page: [])
    assert not any(b.kind == BlockKind.TABLE for b in seen_nothing.blocks)
    text = " ".join(b.text for b in seen_nothing.blocks)
    assert "The inventory file lists every group." in text and "Comment: how is it checked?" in text
    assert any("layout detector" in w for w in seen_nothing.warnings)
    seen_table = extract_pdf(path, layout=lambda page: [("table", (70.0, 98.0, 525.0, 305.0))])
    assert any(b.kind == BlockKind.TABLE for b in seen_table.blocks)
    assert any(b.kind == BlockKind.TABLE for b in extract_pdf(path).blocks)  # no detector: grids as found


def test_each_character_belongs_to_the_smallest_cell_holding_it():
    from parserx.content.pdf_native import _owner

    cells = [(0, 0, 100, 100), (0, 0, 50, 50), (50, 0, 100, 50)]  # a frame drawn around two cells
    assert _owner((20, 20, 30, 30), cells) == 1
    assert _owner((60, 10, 70, 20), cells) == 2
    assert _owner((40, 70, 50, 80), cells) == 0  # only the frame holds it
    assert _owner((200, 200, 210, 210), cells) is None


def test_monospaced_lines_are_measured_from_glyph_widths(tmp_path):
    # P4-6: code is set in a monospaced face; the PDF's font flags do not always say so, the glyph widths do
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "The deploy step runs the following command:", fontsize=11, fontname="helv")
    page.insert_text((72, 130), "parted /dev/sdf -s -- mklabel gpt\nkolla-ansible -i multinode deploy",
                     fontsize=10, fontname="cour")
    page.insert_text((72, 200), "Order 2025 12345 67890", fontsize=11, fontname="helv")
    page.insert_text((72, 240), "工程项目名称：XX-01 标项目 谈判编号：XTGCTP", fontsize=11, fontname="china-s")
    path = tmp_path / "code.pdf"
    doc.save(path)
    ext = extract_pdf(path)
    style = {b.text.split("\n")[0]: b.observations[0].style for b in ext.blocks if b.text}
    assert style["parted /dev/sdf -s -- mklabel gpt"].monospace == "Courier"
    assert style["The deploy step runs the following command:"].monospace is None
    assert style["Order 2025 12345 67890"].monospace is None  # digits do not count: equal-width in most faces
    assert style["工程项目名称：XX-01 标项目 谈判编号：XTGCTP"].monospace is None  # a CJK face's half-width ASCII


def test_monospaced_text_renders_as_a_code_block_with_its_lines():
    from parserx.ir.block import Block
    from parserx.ir.enums import ObservationStatus, TaskKind
    from parserx.ir.observation import Observation, TextStyle
    from parserx.ir.state import DocumentState, PageState
    from parserx.render import render_markdown

    anchor = PdfAnchor(page=1, bbox=(0, 0, 100, 20), coord_space="page_pt")

    def block(bid, order, text, mono, font="Helvetica"):
        obs = Observation(id=f"o-{bid}", engine="native_pdf", engine_version="1", task=TaskKind.EXTRACT, anchor=anchor,
                          text=text, style=TextStyle(monospace=font if mono else None, font=font),
                          status=ObservationStatus.OK)
        return Block(id=bid, kind=BlockKind.TEXT, order=order, anchors=[anchor], observations=[obs],
                     chosen_observation=obs.id, text=text)

    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf", status="complete",
                          pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)],
                          blocks=[block("a", 0, "Run the command below to show the flavor of the node:", False),
                                  block("b", 1, "# comment\nparted /dev/sdf -s\nkolla-ansible deploy", True, "Monaco"),
                                  block("c", 2, "+------+", None, "Monaco"),  # too short to measure, same face
                                  block("d", 3, "| disk | 100 |", True, "Monaco"),
                                  block("e", 4, "Then continue with the next step of the deployment, which checks "
                                                "every node of the cluster and reports what it finds.", False)])
    assert render_markdown(state) == ("<!-- PAGE 1 -->\n\nRun the command below to show the flavor of the node:\n\n"
                                      "```\n# comment\nparted /dev/sdf -s\nkolla-ansible deploy\n+------+\n"
                                      "| disk | 100 |\n```\n\nThen continue with the next step of the deployment, which checks every "
                                      "node of the cluster and reports what it finds.\n")
    # a document without prose in another face has no code by this evidence
    alone = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf", status="complete",
                          pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)],
                          blocks=[block("a", 0, "Plain text printout", True, "Courier")])
    assert "```" not in render_markdown(alone)


def test_code_keeps_its_lines_whatever_role_it_is_given():
    # round 2: an agent set numbered steps with command lines as list items, and the renderer joined the commands
    # into one paragraph; set in a monospaced face, the lines stay a code block (a title stays a title)
    from parserx.ir.block import Block
    from parserx.ir.enums import ObservationStatus, TaskKind
    from parserx.ir.observation import Observation, TextStyle
    from parserx.ir.state import DocumentState, PageState
    from parserx.render import render_markdown

    anchor = PdfAnchor(page=1, bbox=(0, 0, 100, 20), coord_space="page_pt")

    def block(bid, order, text, kind, font):
        obs = Observation(id=f"o-{bid}", engine="native_pdf", engine_version="1", task=TaskKind.EXTRACT, anchor=anchor,
                          text=text, style=TextStyle(monospace=font if font == "Monaco" else None, font=font),
                          status=ObservationStatus.OK)
        return Block(id=bid, kind=kind, order=order, anchors=[anchor], observations=[obs], chosen_observation=obs.id,
                     text=text, level=2 if kind == BlockKind.TITLE else None)

    def state(kind):
        return DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf", status="complete",
                             pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)],
                             blocks=[block("p", 0, "Replace a failed disk of the storage cluster as follows.",
                                           BlockKind.TEXT, "Helvetica"),
                                     block("a", 1, "1. Pause the rebalancing of the cluster:", BlockKind.LIST, "Helvetica"),
                                     block("b", 2, "ceph osd set nobackfill\nceph osd set norebalance", kind, "Monaco")])

    for kind in (BlockKind.TEXT, BlockKind.LIST, BlockKind.OTHER, BlockKind.CAPTION):
        assert "```\nceph osd set nobackfill\nceph osd set norebalance\n```" in render_markdown(state(kind)), kind
    assert "## ceph osd set nobackfill" in render_markdown(state(BlockKind.TITLE))


def _three_line_table(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "The damage cases are listed in the table below, one row per case.", fontsize=10)
    page.draw_line((72, 100), (420, 100))
    y = 115
    for row in (("Case", "Beta1", "Beta2", "Alpha"), ("large", "1.50", "0.60", "1.20"), ("small", "1.50", "0.95", "1.20"),
                ("none", "1.50", "1.00", "1.20")):
        for k, text in enumerate(row):
            page.insert_text((80 + 90 * k, y), text, fontsize=10)
        y += 18
    page.draw_line((72, 120), (420, 120))
    page.draw_line((72, y), (420, y))
    page.insert_text((72, y + 30), "The values follow from the load test of the bridge in 2014.", fontsize=10)
    path = tmp_path / "three_line.pdf"
    doc.save(path)
    return path, (72, 98, 420, y + 2)


def test_a_table_without_ruled_columns_where_the_detector_sees_one(tmp_path):
    # P4-6: three-line tables (horizontal rules only) are read from the alignment of their text inside the region
    # the layout detector marks as a table; both readings must agree (two or more rows and columns)
    path, region = _three_line_table(tmp_path)
    ext = extract_pdf(path, layout=lambda page: [("table", region)])
    tables = [b for b in ext.blocks if b.kind == BlockKind.TABLE]
    assert len(tables) == 1
    rows = [[c.content if c else "" for c in row] for row in tables[0].cells.slot_matrix()]
    assert rows[0] == ["Case", "Beta1", "Beta2", "Alpha"] and rows[-1] == ["none", "1.50", "1.00", "1.20"]
    texts = [b.text for b in ext.blocks if b.kind == BlockKind.TEXT]
    assert any(t.startswith("The damage cases") for t in texts) and any(t.startswith("The values") for t in texts)
    assert all(e.disposition == "output" for e in ext.ledger)
    # without the detector's region the same page is text only
    assert not [b for b in extract_pdf(path, layout=lambda page: []).blocks if b.kind == BlockKind.TABLE]


def test_a_block_of_several_lines_spans_every_line(tmp_path):
    # §11.5 (from v1's line-unwrap merge): joining lines into one block keeps every line's extent in its anchor
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Short\nand a considerably longer middle line\nend", fontsize=11)
    path = tmp_path / "lines.pdf"
    doc.save(path)
    lines = [ln["bbox"] for b in pymupdf.open(path)[0].get_text("dict")["blocks"] for ln in b["lines"]]
    ext = extract_pdf(path)
    (block,) = [b for b in ext.blocks if b.kind == BlockKind.TEXT]
    assert block.text.split("\n") == ["Short", "and a considerably longer middle line", "end"]
    union = (min(b[0] for b in lines), min(b[1] for b in lines), max(b[2] for b in lines), max(b[3] for b in lines))
    assert block.anchors[0].bbox == pytest.approx(union, abs=0.01)
    assert block.anchors[0].bbox[2] > lines[0][2] + 50  # wider than the first line: the middle line widened it


def test_gaps_between_glyphs_are_word_spaces_except_between_ideographs():
    from parserx.content.pdf_native import _join_block_lines, _reconstruct_line_from_chars

    def span(chars, size=10.0):
        return {"size": size, "chars": [{"c": c, "bbox": (x, 0, x + 5, 10)} for c, x in chars]}

    assert _reconstruct_line_from_chars([span([("A", 0), ("B", 5), ("C", 20)])]) == "AB C"  # a gap > size / 4
    assert _reconstruct_line_from_chars([span([("桥", 0), ("梁", 20)])]) == "桥梁"
    # PyMuPDF splits one visual row at a wide gap: the parts join with a space, other rows with a line break
    assert _join_block_lines([("1", (0, 0, 5, 10)), ("Introduction", (40, 0, 120, 10)),
                              ("Body", (0, 20, 30, 30))]) == "1 Introduction\nBody"


def test_a_lines_face_is_the_face_of_its_main_script(tmp_path):
    # a CJK line with inline code is set in its CJK face, however long the code (P8: a step with a command at its
    # end is prose); a line wholly in the code's face is in that face
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(72, 72, 500, 90), "<p>删除故障的逻辑卷 <code>ab</code></p>")
    page.insert_htmlbox(pymupdf.Rect(72, 200, 500, 218), "<p>停止 <code>docker stop ceph_osd_6 now</code></p>")
    page.insert_htmlbox(pymupdf.Rect(72, 300, 500, 318), "<p>4.&nbsp;&nbsp;&nbsp;&nbsp;换盘</p>")  # number and spaces in Latin
    page.insert_htmlbox(pymupdf.Rect(72, 400, 500, 418), "<p><code>ceph osd set nobackfill</code></p>")
    path = tmp_path / "mixed.pdf"
    doc.save(path)
    blocks = {b.text.split()[0]: b.observations[0].style for b in extract_pdf(path).blocks if b.kind == BlockKind.TEXT}
    cjk_face = blocks["删除故障的逻辑卷"].font
    assert blocks["停止"].font == cjk_face and not blocks["停止"].monospace  # prose with a command in it
    assert blocks["4."].font == cjk_face  # the number and the spaces do not outvote the words
    assert "Mono" in blocks["ceph"].font and blocks["ceph"].monospace == blocks["ceph"].font


def test_a_merged_cell_keeps_its_span(tmp_path):
    # tables T2: a title row across the table, and a cell two rows high — PyMuPDF leaves the covered places empty
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xs, ys = [72, 200, 328, 456], [100, 130, 160, 190, 220]
    for y in ys:
        page.draw_line((xs[1] if y == ys[3] else xs[0], y), (xs[-1], y))  # the left cell of rows 2–3 is one
    for x in xs:
        page.draw_line((x, ys[1] if x in xs[1:-1] else ys[0]), (x, ys[-1]))  # the title row is one cell
    for text, (x, y) in (("设备费", (xs[0], ys[0])), ("名称", (xs[0], ys[1])), ("型号", (xs[1], ys[1])),
                         ("金额", (xs[2], ys[1])), ("工作站", (xs[0], ys[2])), ("A1", (xs[1], ys[2])),
                         ("10", (xs[2], ys[2])), ("A2", (xs[1], ys[3])), ("20", (xs[2], ys[3]))):
        page.insert_text((x + 5, y + 20), text, fontsize=11, fontname="china-s")
    doc.save(tmp_path / "merged.pdf")

    grid = next(b for b in extract_pdf(tmp_path / "merged.pdf").blocks if b.kind == BlockKind.TABLE).cells
    spans = {(c.row, c.col): (c.rowspan, c.colspan, c.content) for c in grid.cells if c.content}
    assert spans[(0, 0)] == (1, 3, "设备费") and spans[(2, 0)] == (2, 1, "工作站")
    assert [[c.content if c else None for c in row] for row in grid.slot_matrix()] == [
        ["设备费"] * 3, ["名称", "型号", "金额"], ["工作站", "A1", "10"], ["工作站", "A2", "20"]]
    assert grid.needs_html  # a span has no Markdown form (Q91)


def test_a_three_line_table_keeps_its_head_and_leaves_its_caption_out(tmp_path):
    # tables T4: the head, set taller than the body, stands above where the text alignment's first row starts;
    # a caption the detector's region took in runs across the columns
    from parserx.content.pdf_native import _lines, _unruled_table

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 96), "Tab.1 Errors of the correction factors in each load case", fontsize=9)
    xs = [72, 180, 260, 340]
    for x, text in zip(xs, ("Case", "B1", "B2", "B3")):
        page.insert_text((x, 124), text, fontsize=16)
    for r, row in enumerate((("1+2", "0.25", "1.08", "0.22"), ("2+3", "1.37", "9.17", "7.87"), ("3+1", "0.14", "0.29", "0.02"))):
        for x, text in zip(xs, row):
            page.insert_text((x, 146 + 14 * r), text, fontsize=9)
    doc.save(tmp_path / "three.pdf")
    page = pymupdf.open(tmp_path / "three.pdf")[0]
    lines = _lines(page)
    inside, grid, _ = _unruled_table(page, (60, 84, 420, 190), lines)
    rows = [[c.content for c in sorted((c for c in grid.cells if c.row == r), key=lambda c: c.col)] for r in range(grid.n_rows)]
    assert rows[0] == ["Case", "B1", "B2", "B3"] and rows[1][0] == "1+2" and len(rows) == 4
    assert not any(ln.text.startswith("Tab.1") for ln in inside)  # the caption stays text


def _stored_as(doc, page, text: str, stored: dict[str, str]) -> None:
    """Give the page's CJK font a ToUnicode map that stores some characters as other code points, as a font whose
    radical and ideograph share one glyph does ("用" drawn, "⽤" in the text layer)."""
    entries = "\n".join(f"<{ord(ch):04X}> <{ord(stored.get(ch, ch)):04X}>" for ch in sorted(set(text)))
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def /CMapType 2 def\n"
            f"1 begincodespacerange <0000> <FFFF> endcodespacerange\n{len(set(text))} beginbfchar\n{entries}\n"
            "endbfchar endcmap CMapName currentdict /CMap defineresource pop end end")
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, cmap.encode())
    font = next(f[0] for f in page.get_fonts() if f[4] == "china-s")
    doc.xref_set_key(font, "ToUnicode", f"{xref} 0 R")


def test_radical_code_points_in_the_text_layer_are_read_as_the_ideographs(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    xs, ys = [72, 200, 328], [100, 130, 160]
    _ruled(page, xs, ys)
    for r, row in enumerate([("名称", "金额"), ("甲", "十")]):
        for c, text in enumerate(row):
            page.insert_text((xs[c] + 5, ys[r] + 20), text, fontsize=11, fontname="china-s")
    page.insert_text((72, 300), "使用长度与用途", fontsize=11, fontname="china-s")
    _stored_as(doc, page, "名称金额甲十使用长度与用途", {"用": "⽤", "长": "⻓", "金": "⾦"})
    path = tmp_path / "radicals.pdf"
    doc.save(path)
    assert "使⽤⻓度" in pymupdf.open(path)[0].get_text()  # the text layer stores radicals

    ext = extract_pdf(path)
    text = next(b for b in ext.blocks if b.kind == BlockKind.TEXT)
    assert text.text == text.observations[0].text == "使用长度与用途"
    unified, source = text.decisions
    assert unified.choice == "unified_ideographs" and unified.evidence == {"chars": 3}
    assert unified.reason.endswith("⽤→用 ×2, ⻓→长") and source.choice == "native"  # the source decision stays last
    table = next(b for b in ext.blocks if b.kind == BlockKind.TABLE)
    assert _cells(table) == [["名称", "金额"], ["甲", "十"]]
    assert table.decisions[0].reason.endswith("⾦→金")
    assert sum(e.chars for e in ext.ledger) == 13  # one code point for one


def test_blocks_without_radicals_have_no_such_decision(pdf_path):
    assert all(d.choice != "unified_ideographs" for b in extract_pdf(pdf_path).blocks for d in b.decisions)


def test_a_radical_between_ideographs_is_no_word_gap():
    from parserx.content.pdf_native import _reconstruct_line_from_chars

    # a justified line spaces its glyphs apart; the radical is read as the ideograph first, so no space appears
    span = {"size": 8.0, "chars": [{"c": c, "bbox": (x, 0, x + 8, 10)} for c, x in [("填", 0), ("⼊", 13), ("与", 26)]]}
    assert _reconstruct_line_from_chars([span]) == "填入与"


def test_a_command_the_renderer_wrapped_is_joined_back_and_real_lines_are_not(tmp_path):
    # P9: a renderer wraps a long command at a space; in a shell the two halves would be two commands
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "The deploy step runs the following commands in order:", fontsize=11, fontname="helv")
    page.insert_text((72, 130), "kolla-ansible -i multinode --configdir . --passwords \npasswords.yml --tag ceph deploy\n"
                                "ceph osd set noout\nceph osd set norebalance", fontsize=10, fontname="cour")
    page.insert_text((72, 300), "| name       | value     | \n| properties | cpu_arch  | \n| ram        | 65536     | ",
                     fontsize=10, fontname="cour")
    path = tmp_path / "wrapped.pdf"
    doc.save(path)
    texts = [b.text for b in extract_pdf(path).blocks if b.text]
    command = next(t for t in texts if t.startswith("kolla-ansible"))
    assert command.split("\n") == ["kolla-ansible -i multinode --configdir . --passwords passwords.yml --tag ceph deploy",
                                   "ceph osd set noout", "ceph osd set norebalance"]
    table = next(t for t in texts if t.startswith("| name"))
    assert len(table.split("\n")) == 3  # rows of a table drawn in characters stay rows
