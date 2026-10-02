"""A document whose outline the program has no question about went to no one (F, 2026-10-01: an output with no title,
and one with two levels of numbered headings left as text, were never handed to the agent): the outline is put up
for review where it has no title, or where one-line paragraphs numbered or set like titles are not titles."""

from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import unresolved_items


def _block(bid, order, text, kind=BlockKind.TEXT, level=None):
    anchor = PdfAnchor(page=1, bbox=(0, order * 20, 400, order * 20 + 12), coord_space="page_pt")
    obs = Observation(id=f"o-{bid}", engine="paddleocr", engine_version="v", task=TaskKind.RECOGNIZE, anchor=anchor,
                      text=text, label="text", status=ObservationStatus.OK)
    return Block(id=bid, kind=kind, order=order, anchors=[anchor], text=text, level=level, observations=[obs],
                 chosen_observation=obs.id)


def _state(*blocks):
    return DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf",
                         status=DocumentStatus.IN_PROGRESS, blocks=list(blocks))


def _outline_items(state):
    return [u for u in unresolved_items(state) if u.kind == UnresolvedKind.OUTLINE_REVIEW]


def test_an_output_without_a_title_is_put_up_for_review():
    state = _state(_block("a", 0, "某工程集团有限公司某高速公路物资需求一览表"),
                   _block("b", 1, "本表列出锚具、桥梁伸缩缝、支座的数量与技术要求，供谈判各方参考。"))
    (item,) = _outline_items(state)
    assert item.target == "a" and "no title" in item.detail


def test_numbered_one_line_paragraphs_that_are_not_titles_are_put_up_for_review():
    state = _state(_block("h", 0, "一、企业基本情况", BlockKind.TITLE, level=2),
                   _block("a", 1, "（一）企业情况简介。"),
                   _block("b", 2, "企业成立于二〇一〇年，主营桥梁监测设备的研发、生产与销售。"),
                   _block("c", 3, "（二）企业营业执照。"))
    (item,) = _outline_items(state)
    assert item.target == "a" and [q.doc_text for q in item.quotes] == ["（一）企业情况简介。", "（二）企业营业执照。"]
    assert "2 one-line" in item.detail


def test_an_outline_with_nothing_to_ask_raises_no_item():
    state = _state(_block("h", 0, "一、企业基本情况", BlockKind.TITLE, level=2),
                   _block("b", 1, "企业成立于二〇一〇年，主营桥梁监测设备的研发、生产与销售。"))
    assert _outline_items(state) == []
