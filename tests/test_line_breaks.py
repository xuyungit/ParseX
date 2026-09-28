"""Line breaks of scanned text read as one run, put back from the local reading (P4)."""

import pytest

from parserx.content.line_breaks import _with_breaks, restore_line_breaks
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.rotation import unturned
from parserx.ir.state import DocumentState, PageReading, PageState, ReadLine

BOX = (70.0, 100.0, 460.0, 400.0)


def _lines(*rows):
    """(text, right edge, left edge) per row, one row under the other."""
    return [(ReadLine(bbox=(left, 110 + 15 * i, right, 122 + 15 * i), text=text, score=0.99), BOX)
            for i, (text, right, left) in enumerate(rows)]


def test_items_the_page_puts_on_lines_of_their_own_get_their_breaks_back():
    text = "2022.01~2022.03：市场调研；2022.04~2022.12：产品试制；2023.01~2023.08：根据试验结果调整设计、再试验，申请专利；2023.09~2024.01：结题；"
    lines = _lines(("2022.01~2022.03：市场调研；", 250, 72), ("2022.04~2022.12：产品试制；", 260, 72),
                   ("2023.01~2023.08：根据试验结果调整设计、再试验，申请专利；", 455, 72),  # runs to the edge: the next item
                   ("2023.09~2024.01：结题；", 200, 72))
    assert _with_breaks(text, lines).split("\n") == ["2022.01~2022.03：市场调研；", "2022.04~2022.12：产品试制；",
                                                     "2023.01~2023.08：根据试验结果调整设计、再试验，申请专利；",
                                                     "2023.09~2024.01：结题；"]
    assert _with_breaks("2、创新点（1）采用新型材料（2）合理的设计", _lines(
        ("2、创新点", 150, 72), ("（1）采用新型材料", 220, 72), ("（2）合理的设计", 210, 72))) == \
        "2、创新点\n（1）采用新型材料\n（2）合理的设计"


def test_text_that_wraps_narrow_or_a_look_alike_line_adds_no_break():
    wrapped = "contracts in bonds dealt in and interest made prior to the third business day"
    assert _with_breaks(wrapped, _lines(("contracts in bonds dealt in and", 250, 72),
                                        ("interest made prior to the third", 255, 72), ("business day", 150, 72))) == wrapped
    # another cell's line that starts alike is not this text's first line
    assert _with_breaks("2022.01~2022.03：调研；2022.04：试制；", _lines(
        ("2022.01—2024.01", 150, 72), ("2022.01~2022.03：调研；", 200, 72), ("2022.04：试制；", 180, 72))) == \
        "2022.01~2022.03：调研；\n2022.04：试制；"


@pytest.mark.parametrize("rotation", [0, 270])  # 270: boxes in the unrotated page, judged as the page is shown
def test_a_scanned_text_block_is_split_at_the_breaks_into_paragraphs(rotation):
    page = PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842), rotation=rotation)
    anchor = PdfAnchor(page=1, bbox=unturned(page, BOX), coord_space="page_pt")
    text = "第一段结束了。第二段从这里开始并且结束。"
    block = Block(id="b-p001-0001", kind=BlockKind.TEXT, order=0, anchors=[anchor], text=text,
                  observations=[Observation(id="o-1", engine="paddleocr", engine_version="v", task=TaskKind.RECOGNIZE,
                                            anchor=anchor, text=text, status=ObservationStatus.OK)],
                  chosen_observation="o-1")
    reading = PageReading(n=1, engine="local", dpi=150, lines=[
        r.model_copy(update={"bbox": unturned(page, r.bbox)})
        for r, _ in _lines(("第一段结束了。", 180, 72), ("第二段从这里开始并且结束。", 300, 72))])
    state = DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, blocks=[block], readings=[reading], pages=[page])
    assert restore_line_breaks(state) == ["b-p001-0001"]
    assert [b.text for b in sorted(state.blocks, key=lambda b: b.order)] == ["第一段结束了。", "第二段从这里开始并且结束。"]
