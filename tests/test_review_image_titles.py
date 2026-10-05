"""A title read inside an image is written in bold whatever its level (render/markdown._image_text): its level is
no review item (Q157); a title of the page itself still is."""

from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, ImageRoute, PageStatus, RelationKind
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, ImageRecord, PageState
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import unresolved_items


def _block(bid, kind, anchor, text=""):
    return Block(id=bid, kind=kind, order=0, status=BlockStatus.OK, anchors=[anchor], text=text)


def test_only_a_title_of_the_page_is_a_pending_title_level():
    page = PdfAnchor(page=1, bbox=(50, 50, 300, 70), coord_space="page_pt")
    inner = AssetAnchor(asset="a-1", bbox=(0, 0, 400, 40), image_size=(600, 800))
    blocks = [_block("b-title", BlockKind.TITLE, page, "第三章 试验"),
              _block("b-img", BlockKind.FIGURE, PdfAnchor(page=1, bbox=(50, 100, 500, 700), coord_space="page_pt")),
              _block("b-in", BlockKind.TITLE, inner, "检测报告")]
    state = DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))], blocks=blocks,
        images=[ImageRecord(id="a-1", route=ImageRoute.SCAN, shown=True)],
        relations=[Relation(id="r-1", kind=RelationKind.CONTAINS, src="b-img", dst="b-in")])
    pending = [u.target for u in unresolved_items(state) if u.kind == UnresolvedKind.STRUCTURE_PENDING]
    assert pending == ["b-title"]


def test_the_outline_leaves_out_what_is_read_inside_images():
    # 76 of 177 outline lines of the medium bid document were titles inside images, written in bold whatever their
    # level: the agent set their levels in batches of 35 and 85, to no effect (Q164 item 4)
    from parserx.tools.draft import outline_blocks, title_like

    page = PdfAnchor(page=1, bbox=(50, 50, 300, 70), coord_space="page_pt")
    inner = AssetAnchor(asset="a-1", bbox=(0, 0, 400, 40), image_size=(600, 800))
    blocks = [_block("b-title", BlockKind.TITLE, page, "第三章 试验"),
              _block("b-img", BlockKind.FIGURE, PdfAnchor(page=1, bbox=(50, 100, 500, 700), coord_space="page_pt")),
              _block("b-in", BlockKind.TITLE, inner, "检测报告"), _block("b-in2", BlockKind.TEXT, inner, "1. 范围")]
    state = DocumentState(
        id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
        pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))], blocks=blocks,
        images=[ImageRecord(id="a-1", route=ImageRoute.SCAN, shown=True)],
        relations=[Relation(id=f"r-{b}", kind=RelationKind.CONTAINS, src="b-img", dst=b) for b in ("b-in", "b-in2")])
    assert {b.id for b in outline_blocks(state)} == {"b-title", "b-img"}
    assert all(b.id not in ("b-in", "b-in2") for b in title_like(state))
