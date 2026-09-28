"""Pages a /Rotate turns: every box in the unrotated page, renders and crops as the page is shown (ir/rotation.py)."""

import io

import pymupdf
import pytest
from PIL import Image

from parserx.content.furniture import mark_scan_furniture
from parserx.content.pdf_native import extract_pdf
from parserx.content.scan import PageScan, page_blocks, render_page
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.rotation import onto_page, shown, transform_box, unturned, whole
from parserx.ir.state import DocumentState, PageReading, PageState, ReadLine
from parserx.reading.compare import text_near
from parserx.tools.imaging import page_render, region_crop
from parserx.tools.page_reading import _page_box

RED = (300.0, 200.0, 400.0, 260.0)  # a filled box on the unrotated page
TURNED = PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595.0, 842.0), rotation=270)


@pytest.fixture
def turned_pdf(tmp_path):
    """A landscape page (842 × 595) that /Rotate 270 shows upright as a portrait page, as a scanning app saves it."""
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.draw_rect(pymupdf.Rect(RED), color=(1, 0, 0), fill=(1, 0, 0))
    page.insert_text((100, 100), "Rotated page", fontsize=12)
    page.set_rotation(270)
    path = tmp_path / "turned.pdf"
    doc.save(path)
    return path


def _red_share(png: bytes) -> float:
    with Image.open(io.BytesIO(png)) as image:
        data = image.convert("RGB").tobytes()
    pixels = [data[i:i + 3] for i in range(0, len(data), 3)]
    return sum(1 for r, g, b in pixels if r > 200 and g < 60 and b < 60) / len(pixels)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_boxes_turn_as_the_pdf_library_turns_them(rotation):
    page = pymupdf.open().new_page(width=842, height=595)
    page.set_rotation(rotation)
    state = PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(page.rect.width, page.rect.height),
                      rotation=rotation)
    assert shown(state, RED) == pytest.approx(tuple(pymupdf.Rect(RED) * page.rotation_matrix))
    assert unturned(state, shown(state, RED)) == pytest.approx(RED)
    assert whole(state) == pytest.approx((0.0, 0.0, 842.0, 595.0))
    render = (0.5, 0.0, 0.0, 0.5, 10.0, 20.0)  # render pixels → points of the page as shown
    assert transform_box(onto_page(state, render), (40, 60, 80, 100)) == \
        pytest.approx(unturned(state, transform_box(render, (40, 60, 80, 100))))
    if not rotation:  # an unturned page: the very same values
        assert shown(state, RED) is RED and unturned(state, RED) is RED and onto_page(state, render) is render


def test_native_pages_record_their_turn(turned_pdf):
    page = extract_pdf(turned_pdf).pages[0]
    assert (page.rotation, page.size_pt) == (270, (595.0, 842.0))


def _engine_reading(turned_pdf):
    """The scan engine's reading of the red box, on a render of the page as shown at 2 px per point."""
    with pymupdf.open(turned_pdf) as doc:
        image = render_page(doc, 1, 1190)
        px = tuple(v * 2 for v in pymupdf.Rect(RED) * doc[0].rotation_matrix)
        back = doc[0].derotation_matrix
    entries = [{"block_label": "text", "block_content": "红色方块", "block_bbox": list(px), "block_order": 1},
               {"block_label": "image", "block_content": "", "block_bbox": list(px), "block_order": None}]
    scan = PageScan(page=1, raw={"prunedResult": {"width": 1190, "height": 1684, "parsing_res_list": entries}},
                    raw_ref="k" * 64, engine_version="v")
    return page_blocks(scan, page_size=TURNED.size_pt, first_seq=1, first_item=1, page_image=image,
                       page=TURNED), px, back


def test_scan_engine_boxes_come_back_to_the_unrotated_page(turned_pdf):
    result, _, _ = _engine_reading(turned_pdf)
    text = next(b for b in result.blocks if b.kind == BlockKind.TEXT)
    figure = next(b for b in result.blocks if b.kind == BlockKind.FIGURE)
    assert text.anchors[0].bbox == pytest.approx(RED) and figure.anchors[0].bbox == pytest.approx(RED)
    pixels = text.observations[0].anchor
    assert transform_box(pixels.transform, pixels.bbox) == pytest.approx(RED)  # image px → unrotated page points
    crop = next(a for a in result.assets if a.role == "crop")
    assert crop.source.bbox == pytest.approx(RED) and _red_share(result.asset_bytes[crop.path]) > 0.95


def test_the_local_reading_and_the_scan_engine_meet(turned_pdf):
    result, px, back = _engine_reading(turned_pdf)
    text = next(b for b in result.blocks if b.kind == BlockKind.TEXT)
    line = ReadLine(bbox=_page_box(px, 144, back), text="红色方块", score=0.99)  # the local reader, same render
    state = DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, pages=[TURNED], blocks=[text],
                          readings=[PageReading(n=1, engine="local", dpi=144, lines=[line])])
    assert text_near(state, text) == "红色方块"


def test_crops_of_a_turned_page_show_the_box(turned_pdf):
    crop, png, transform, render, _ = region_crop(turned_pdf, TURNED, RED, 72, 0.0)
    assert crop.source.bbox == pytest.approx(RED) and _red_share(png) > 0.95
    assert transform_box(transform, (0, 0, crop.width, crop.height)) == pytest.approx(RED)
    asset, _, to_page = page_render(turned_pdf, TURNED, 72)
    assert (asset.width, asset.height) == (595, 842)  # the page as shown
    assert transform_box(to_page, (0, 0, asset.width, asset.height)) == pytest.approx(whole(TURNED))


def test_margins_are_those_of_the_page_as_shown():
    def scanned(bid, page, shown_box, text):
        anchor = PdfAnchor(page=page, bbox=unturned(TURNED, shown_box), coord_space="page_pt")
        return Block(id=bid, kind=BlockKind.TEXT, order=0, anchors=[anchor], text=text, observations=[Observation(
            id=f"o-{bid}", engine="paddleocr", engine_version="v", task=TaskKind.RECOGNIZE, anchor=anchor, text=text,
            status=ObservationStatus.OK)])

    pages = [TURNED, TURNED.model_copy(update={"n": 2})]
    blocks = [scanned("h1", 1, (100, 20, 400, 40), "立项报告"), scanned("h2", 2, (100, 21, 400, 41), "立项报告"),
              scanned("b1", 1, (72, 100, 500, 120), "正文"), scanned("b2", 2, (72, 100, 500, 120), "正文")]
    state = DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, pages=pages, blocks=blocks)
    assert sorted(mark_scan_furniture(state)) == ["h1", "h2"]
    assert {b.kind for b in blocks[:2]} == {BlockKind.HEADER}  # the top of the page as shown, not its right edge
