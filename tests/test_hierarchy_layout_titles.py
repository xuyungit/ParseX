"""Titles on native pages from two independent signals (Phase 3 D4): the layout detector's section-title label and
typography set apart from the body text."""

from parserx.hierarchy.layout_titles import layout_titles
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation, TextStyle
from parserx.ir.state import DocumentState, PageReading, PageState

_BODY = TextStyle(font_size=12.0, bold=False, font="SimSun")


def _block(bid, order, text, style=_BODY, label=None, kind=BlockKind.TEXT, y=100):
    anchor = PdfAnchor(page=1, bbox=(65, y, 300, y + 12), coord_space="page_pt")
    observations = [Observation(id=f"o-{bid}", engine="native_pdf", engine_version="v", task=TaskKind.EXTRACT,
                                anchor=anchor, text=text, style=style, status=ObservationStatus.OK)]
    if label:
        observations.append(Observation(id=f"o-{bid}-layout", engine="layout", engine_version="v", task=TaskKind.LAYOUT,
                                        anchor=anchor, label=label, status=ObservationStatus.OK))
    return Block(id=bid, kind=kind, order=order, anchors=[anchor], observations=observations,
                 chosen_observation=f"o-{bid}", text=text)


def _state(blocks, not_prose=()):
    body = [_block(f"b-body{i}", 100 + i, "本发明属于路桥工程技术领域，具体来说，涉及到一种识别连续梁桥实际刚度的方法。" * 3, y=400 + i)
            for i in range(4)]
    return DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf", status=DocumentStatus.IN_PROGRESS,
                         pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))],
                         blocks=[*blocks, *body],
                         readings=[PageReading(n=1, engine="local", dpi=150, lines=[], not_prose=list(not_prose))])


_HEADING = TextStyle(font_size=12.0, bold=False, font="SimHei")


def test_a_section_title_label_on_text_set_apart_from_the_body_is_a_title():
    found = layout_titles(_state([_block("b-1", 0, "技术领域", _HEADING, "paragraph_title"),
                                  _block("b-2", 1, "5.4 Optimized Libraries", TextStyle(font_size=14.0, font="SimSun"),
                                         "paragraph_title", y=200)]))
    assert [(bid, level) for bid, _, level, _ in found] == [("b-1", 2), ("b-2", 3)]  # rank 2, one dotted level more
    assert found[0][3]["label"] == "paragraph_title" and "font" in found[0][3]["set_apart"]


def test_one_signal_alone_is_not_enough():
    assert layout_titles(_state([_block("b-1", 0, "技术领域", _BODY, "paragraph_title")])) == []  # body typography
    assert layout_titles(_state([_block("b-1", 0, "技术领域", _HEADING)])) == []  # no title label


def test_document_titles_multi_line_blocks_pictures_and_titles_are_left_alone():
    blocks = [
        _block("b-1", 0, "一种基于静载试验识别连续梁桥实际刚度的方法", _HEADING, "doc_title"),  # a document title
        _block("b-2", 1, "2.2.1 不同工况组合下的比较\n考虑表 3 中的工况", _HEADING, "paragraph_title", y=150),
        _block("b-3", 2, "Variables", _HEADING, "paragraph_title", y=500),  # a label inside a figure
        _block("b-4", 3, "背景技术", _HEADING, "paragraph_title", kind=BlockKind.TITLE, y=200),
        _block("b-5", 4, " ", _HEADING, "paragraph_title", y=250),
    ]
    assert layout_titles(_state(blocks, not_prose=[(60, 490, 320, 520)])) == []


def test_candidates_are_listed_for_the_agent_not_applied():
    from parserx.tools.envelope import UnresolvedKind
    from parserx.tools.views import unresolved_items

    state = _state([_block("b-1", 0, "技术领域", _HEADING, "paragraph_title")])
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.TITLE_CANDIDATE]
    assert [(u.target, [q.doc_text for q in u.quotes]) for u in items] == [("b-1", ["技术领域"])]
    assert "font SimHei" in items[0].detail and state.blocks[0].kind == BlockKind.TEXT
