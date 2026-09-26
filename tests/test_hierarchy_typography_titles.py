"""Titles from independent evidence that agrees (Q72): typography, the layout detector's label, numbering, nesting."""

from parserx.hierarchy.typography_titles import typography_titles
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.observation import Observation, TextStyle
from parserx.ir.state import DocumentState, PageState

BODY = TextStyle(font_size=12.0, bold=False, font="SimSun")
BOLD = TextStyle(font_size=12.0, bold=True, font="SimSun")
LARGE = TextStyle(font_size=16.0, bold=True, font="SimSun")
SENTENCE = "本发明属于路桥工程技术领域，具体来说，涉及到一种识别连续梁桥实际刚度的方法。" * 3


def _block(bid, text, style=BODY, label=None, engine="native_pdf"):
    anchor = PdfAnchor(page=1, bbox=(65, 100, 300, 112), coord_space="page_pt")
    observations = [Observation(id=f"o-{bid}", engine=engine, engine_version="v", task=TaskKind.EXTRACT,
                                anchor=anchor, text=text, style=style, status=ObservationStatus.OK)]
    if label:
        observations.append(Observation(id=f"o-{bid}-layout", engine="layout", engine_version="v",
                                        task=TaskKind.LAYOUT, anchor=anchor, label=label, status=ObservationStatus.OK))
    return Block(id=bid, kind=BlockKind.TEXT, order=0, anchors=[anchor], observations=observations,
                 chosen_observation=f"o-{bid}", text=text)


def _titles(*blocks, **kw):
    blocks = [*blocks, *(_block(f"body{i}", SENTENCE) for i in range(4))]
    for order, block in enumerate(blocks):
        block.order = order
    state = DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS, blocks=blocks,
                          pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))])
    return {bid: level for bid, _text, level, _ev in typography_titles(state, **kw)}


def test_two_kinds_of_evidence_make_a_title_one_does_not():
    found = _titles(
        _block("typo+layout", "技术领域", BOLD, "paragraph_title"),
        _block("typo+number", "二、主要标准", BOLD),
        _block("layout+number", "三、产品结构", BODY, "paragraph_title"),
        _block("typo", "注意事项", BOLD),  # a bold phrase
        _block("number", "1、定位产品一定要按要求来定位", BODY),  # a list item
        _block("layout", "附图说明", BODY, "paragraph_title"),
    )
    assert set(found) == {"typo+layout", "typo+number", "layout+number"}


def test_numbering_needs_words_and_no_finished_sentence():
    found = _titles(
        _block("sentence", "1、产品外观要求要严格执行，按工艺标准来处理。", BOLD),
        _block("number-only", "193.", BOLD),
        _block("date", "2026.1.26", BOLD),
        _block("cut", "G. Frequency Response Function Shape-based Meth-", BOLD),
        _block("title", "3.1 测力原理", BOLD),
    )
    assert set(found) == {"title"}


def test_the_layout_label_must_cover_the_whole_paragraph():
    block = _block("mixed", "作者\n摘要", LARGE, "paragraph_title")
    block.observations.append(block.observations[-1].model_copy(update={"id": "o-mixed-layout2", "label": "text"}))
    assert _titles(block) == {}
    assert _titles(_block("wrapped", "预制装配式板梁桥的\n模型修正方法", LARGE, "doc_title")) == {"wrapped": 1}


def test_a_number_extending_a_titles_number_nests_in_the_outline():
    found = _titles(_block("s5", "5、产品技术要求", BOLD), _block("s51", "5.1 材料", BODY),
                    _block("s511", "5.1.1 支座主要材料", BODY), _block("other", "6.1 铸钢件", BODY))
    assert set(found) == {"s5", "s51", "s511"}  # 6.1 has no title 6 above it


def test_levels_follow_the_documents_own_typography_under_its_title():
    found = _titles(_block("title", "某型支座技术规范", LARGE),  # opens the document, set largest
                    _block("h1", "1 概述", TextStyle(font_size=14.0, bold=True, font="SimSun")),
                    _block("h2", "1.1 范围", BOLD), _block("h3", "（一）适用对象", BOLD, "paragraph_title"))
    assert found == {"title": 1, "h1": 2, "h2": 3, "h3": 3}  # h3: same typography as h2 (numbering unifies later)
    # an unnumbered opening line set like the unnumbered titles after it is their sibling, not the title over them
    chapter = TextStyle(font_size=16.0, bold=False, font="SimSun")
    found = _titles(_block("c1", "Chapter One", chapter), _block("c2", "Chapter Two", chapter, "paragraph_title"))
    assert found == {"c1": 1, "c2": 1}
    # another source (the scan engine, a Title style) already gave the document its title
    assert _titles(_block("h1", "1 概述", BOLD), titled=True) == {"h1": 2}
    assert _titles(_block("h1", "1 概述", BOLD)) == {"h1": 1}
    # the scan engine's labels rank sections below a title: native titles of the same document use that scale
    assert _titles(_block("h1", "1 概述", BOLD), others={"body3"}) == {"h1": 1}  # it opens the document: its title
    scanned_first = _block("scan", "一、概况", BODY)  # stands for a title the scan engine read before it
    assert _titles(scanned_first, _block("h1", "1 概述", BOLD), others={"scan"}) == {"h1": 2}


def test_a_contents_entry_is_not_a_second_title():
    found = _titles(_block("toc", "1 概述 ........ 3", BOLD), _block("toc2", "2 标准 5", BOLD),
                    _block("h1", "1 概述", BOLD), _block("h2", "2 标准", BOLD))
    assert set(found) == {"h1", "h2"}


def test_code_does_not_set_the_body_typography():
    code = TextStyle(font_size=11.0, bold=False, font="Monaco", monospace="Monaco")
    blocks = [_block(f"code{i}", "docker stop ceph_osd_6 && docker rm ceph_osd_6 --force" * 3, code)
              for i in range(8)]
    assert _titles(*blocks, _block("prose", "1. 暂停自平衡", BODY)) == {}  # prose is not "set apart" from code
