"""A paragraph the scan engine ran on from one region of a page into another (milestone run, 2026-10-02): its block
has both places — a look at either is evidence of it, and its image is both, one under another."""

import io

from PIL import Image

from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus
from parserx.ir.evidence import Evidence
from parserx.ir.state import DocumentState
from parserx.tools.evidence import image_evidence
from parserx.tools.imaging import places, stacked

LEFT, RIGHT = (42.0, 543.0, 290.0, 737.0), (304.0, 68.0, 551.0, 283.0)


def _block():
    return Block(id="b-p001-0010", kind=BlockKind.TEXT, order=0, text="…", anchors=[
        PdfAnchor(page=1, bbox=LEFT, coord_space="page_pt"), PdfAnchor(page=1, bbox=RIGHT, coord_space="page_pt"),
        PdfAnchor(page=2, bbox=LEFT, coord_space="page_pt")])


def test_a_look_at_where_the_paragraph_runs_on_is_evidence_of_it():
    block = _block()
    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, blocks=[block], evidence=[
                              Evidence(id="e-1", how="image", page=1, bbox=(300.0, 65.0, 555.0, 285.0), image="a-1")])
    assert image_evidence(state, block, "e-1").passed


def test_the_places_of_a_block_on_its_page_are_its_image_one_under_another():
    assert [a.bbox for a in places(_block())] == [LEFT, RIGHT]  # the first page it is on
    images = []
    for size in ((40, 10), (30, 20)):
        buf = io.BytesIO()
        Image.new("RGB", size, "black").save(buf, "PNG")
        images.append(buf.getvalue())
    data, width, height = stacked(images, gap=4)
    assert (width, height) == (40, 34) and Image.open(io.BytesIO(data)).size == (40, 34)
