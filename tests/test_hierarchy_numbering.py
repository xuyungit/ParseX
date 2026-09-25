"""Numbering as structural evidence (Phase 3 D4): a dotted number nests under the number it extends; a gap in a
numbered title sequence points at the paragraph that fills it."""

from parserx.hierarchy.levels import unify_levels
from parserx.hierarchy.numbering_gaps import numbering_gaps
from parserx.ir.anchor import DocxAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, PageState


def test_a_dotted_number_is_one_level_below_the_number_it_extends():
    titles = [("a", "5、产品技术要求", 2), ("b", "5.1 材料", 3), ("c", "5.1.1支座主要材料见下表", 3),
              ("d", "5.1.2技术要求", 3), ("e", "5.2.1 铸造及探伤", 3), ("f", "5.3 支座的装配", 3)]
    assert unify_levels(titles) == {"a": 2, "b": 3, "c": 4, "d": 4, "e": 4, "f": 3}


def _doc(paragraphs):
    blocks = []
    for i, (text, level) in enumerate(paragraphs):
        blocks.append(Block(id=f"b-d{i:05d}", kind=BlockKind.TITLE if level else BlockKind.TEXT, order=i, level=level,
                            anchors=[DocxAnchor(part="word/document.xml", node_path=f"/w:body/w:p[{i + 1}]")],
                            text=text))
    return DocumentState(id="d", source="x.docx", source_sha256="0" * 64, format="docx",
                         status=DocumentStatus.IN_PROGRESS,
                         pages=[PageState(n=1, unit="docx_segment", status=PageStatus.DONE)], blocks=blocks)


def test_the_paragraph_filling_a_gap_in_a_numbered_sequence_is_a_candidate():
    state = _doc([("5、产品技术要求", 2), ("5.1 材料", 3), ("正文。", None), ("5.2支座加工：", None),
                  ("5.2.1 铸造及探伤", 4), ("5.3 支座的装配", 3), ("6支座质量控制方法", None),
                  ("6.1 铸钢件", 3), ("6.2 改性超高分子聚乙烯板", 3)])
    found = {bid: (text, level) for bid, text, level, _ in numbering_gaps(state)}
    assert found == {"b-d00003": ("5.2支座加工：", 3), "b-d00006": ("6支座质量控制方法", 2)}


def test_no_candidate_without_a_gap_or_a_paragraph_that_fills_it():
    state = _doc([("1 概述", 2), ("1.1 范围", 3), ("正文提到 1.3 节的内容。", None), ("1.2 术语", 3),
                  ("2 要求", 2), ("本节规定 3 件样品。", None), ("3.1 取样", 3)])
    assert numbering_gaps(state) == []  # "3" is missing, but no paragraph starts with it


def test_gap_fillers_are_listed_for_the_agent():
    from parserx.tools.envelope import UnresolvedKind
    from parserx.tools.views import unresolved_items

    state = _doc([("5.1 材料", 3), ("5.2支座加工：", None), ("5.3 支座的装配", 3)])
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.TITLE_CANDIDATE]
    assert [(u.target, [q.doc_text for q in u.quotes]) for u in items] == [("b-d00001", ["5.2支座加工："])]
    assert "between 5.1 and 5.3" in items[0].detail
