"""Bulleted list items (R1): a bullet character, a drawn mark on a native page, a solid mark on a scanned page."""

import numpy as np
import pymupdf

from parserx.content.lists import _scanned_mark, bulleted, mark_list_items
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation, TextStyle
from parserx.ir.state import DocumentState, PageState
from parserx.render.markdown import _render


def _native(bid, text, bbox):
    anchor = PdfAnchor(page=1, bbox=bbox, coord_space="page_pt")
    obs = Observation(id=f"o-{bid}", engine="native_pdf", engine_version="v", task=TaskKind.EXTRACT, anchor=anchor,
                      text=text, style=TextStyle(font_size=12.0), status=ObservationStatus.OK)
    return Block(id=bid, kind=BlockKind.TEXT, order=0, anchors=[anchor], observations=[obs],
                 chosen_observation=obs.id, text=text)


def test_a_drawn_dot_or_a_bullet_character_before_a_paragraph_makes_a_list_item(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_circle((80, 106), 2.5, color=(0, 0, 0), fill=(0, 0, 0))  # a web page's bullet: a vector dot
    page.draw_circle((80, 206), 2.5, color=(0, 0, 0), fill=(0, 0, 0))  # beside a diagram's "..."
    path = tmp_path / "list.pdf"
    doc.save(path)
    blocks = [_native("dot", "价格调整（是否有降价现象）", (96, 99, 400, 113)),
              _native("char", "• 新产品推出", (96, 130, 400, 144)),
              _native("plain", "正文段落，没有项目符号。", (96, 160, 400, 174)),
              _native("dots", "...", (96, 199, 120, 213))]
    state = DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, blocks=blocks,
                          pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))])
    assert sorted(mark_list_items(state, path)) == ["char", "dot"]
    by_id = {b.id: b for b in state.blocks}
    assert all(bulleted(by_id[b]) for b in ("dot", "char"))
    assert _render(by_id["dot"], {}, "images") == "- 价格调整（是否有降价现象）"
    assert _render(by_id["char"], {}, "images") == "- 新产品推出"


def test_a_scanned_bullet_is_a_solid_square_followed_by_a_gap():
    image = np.full((60, 200), 255, np.uint8)
    image[20:32, 10:22] = 0  # a solid ■, then words
    image[18:34, 40:44] = 0
    image[18:34, 50:60] = 0
    assert _scanned_mark((image, 1.0), (10, 15, 190, 40), 20.0)
    stroke = np.full((60, 200), 255, np.uint8)
    stroke[18:34, 10:13] = 0  # a letter's stroke: thin, not square
    stroke[18:21, 10:22] = 0
    assert not _scanned_mark((stroke, 1.0), (10, 15, 190, 40), 20.0)
