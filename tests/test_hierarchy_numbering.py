"""Numbering as structural evidence (Phase 3 D4): a dotted number nests under the number it extends; a gap in a
numbered title sequence points at the paragraph that fills it."""

from parserx.hierarchy.levels import unify_levels
from parserx.hierarchy.numbering_gaps import numbering_gaps, series_successors, unclear_nesting
from parserx.ir.anchor import DocxAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, PageState


def test_a_dotted_number_is_one_level_below_the_number_it_extends():
    titles = [("a", "5、产品技术要求", 2), ("b", "5.1 材料", 3), ("c", "5.1.1支座主要材料见下表", 3),
              ("d", "5.1.2技术要求", 3), ("e", "5.2.1 铸造及探伤", 3), ("f", "5.3 支座的装配", 3)]
    assert unify_levels(titles) == {"a": 2, "b": 3, "c": 4, "d": 4, "e": 4, "f": 3}


def test_a_numbering_style_first_seen_under_another_is_one_level_below_it():
    # P4-3: the document's own order says how its numbering styles nest — no table of styles
    titles = [("a", "一、总则", 1), ("b", "（一）范围", 1), ("c", "1. 适用对象", 1), ("d", "（1）新建工程", 1),
              ("e", "（2）改建工程", 1), ("f", "2. 术语", 1), ("g", "（二）要求", 1), ("h", "二、方法", 1),
              ("i", "（一）试验", 1)]
    assert unify_levels(titles) == {"a": 1, "b": 2, "c": 3, "d": 4, "e": 4, "f": 3, "g": 2, "h": 1, "i": 2}
    # the same styles in another order nest the other way
    titles = [("a", "1. 概述", 1), ("b", "一、背景", 1), ("c", "二、目标", 1), ("d", "2. 方法", 1)]
    assert unify_levels(titles) == {"a": 1, "b": 2, "c": 2, "d": 1}
    # a proposal above or below the style over it is the typography's evidence and stands
    titles = [("a", "第一章 总则", 1), ("b", "前言", 2), ("c", "1 范围", 3), ("d", "第二章 设计", 1)]
    assert unify_levels(titles) == {"a": 1, "b": 2, "c": 3, "d": 1}
    titles = [("a", "二、资格条件", 2), ("b", "八、采购人", 2), ("c", "第二章 供应商须知", 1), ("d", "1. 总则", 2)]
    assert unify_levels(titles) == {"a": 2, "b": 2, "c": 1, "d": 2}
    # a new style under another nests even when the titles before it in its list were not found
    titles = [("a", "四、项目计划", 2), ("b", "五、经费预算", 2), ("c", "（五）各科目预算说明表", 2)]
    assert unify_levels(titles) == {"a": 2, "b": 2, "c": 3}
    # a number continuing the one above is a sibling in another style, not a new list
    titles = [("a", "1 概述", 2), ("b", "2 主要标准", 2), ("c", "3、产品结构", 2), ("d", "4、产品特点", 2)]
    assert unify_levels(titles) == {"a": 2, "b": 2, "c": 2, "d": 2}


def test_levels_a_file_declares_stay_and_the_others_fit_around_them():
    # a DOCX outline level is the file's own statement; titles found by other evidence take levels around it
    titles = [("t", "采购文件", 1), ("a", "二、资格条件", 7), ("b", "第二章 供应商须知", 1), ("c", "1. 总则", 5),
              ("d", "1.1 项目概况", 5)]
    assert unify_levels(titles, fixed={"b"}) == {"t": 1, "a": 2, "b": 1, "c": 2, "d": 3}
    titles += [("e", "第三章 技术规格书", 4), ("f", "1.锚具", 5)]  # the declared chapter settles its pattern
    assert unify_levels(titles, fixed={"b"})["e"] == 1
    titles = [("a", "二、资格条件", 1), ("b", "第二章 供应商须知", 1), ("c", "1. 总则", 2)]
    assert unify_levels(titles)["b"] == 2  # unfixed, the numbering nesting moves the chapter under "二、"
    assert unify_levels(titles, fixed={"b"})["b"] == 1


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


def test_the_next_number_of_a_title_series_is_a_candidate():
    # P7: a scan engine labels "C. …" body text; the title "B. …" before it says what it is
    state = _doc([("B. Self-Regulatory Organization's Statement on Burden on Competition", 2), ("Body text.", None),
                  ("C. Self-Regulatory Organization's Statement on Comments", None), ("More body text.", None),
                  ("III. Date of Effectiveness", 2), ("第二章 供应商须知 10", 1), ("第三章 技术规格书 15", None)])
    found = {bid: (text[:2], level) for bid, text, level, _ in series_successors(state)}
    assert found == {"b-d00002": ("C.", 2)}  # not the contents entry "第三章 … 15"


def test_two_numbering_styles_of_a_text_cut_from_a_document_are_flagged():
    # P7: a page opening with "B." and then "III.": which is outer the numbers cannot tell
    cut = _doc([("B. Statement on Burden", 2), ("C. Statement on Comments", 2), ("III. Date of Effectiveness", 2)])
    assert [(bid, above[:2]) for bid, _t, above in unclear_nesting(cut)] == [("b-d00002", "C.")]
    whole = _doc([("一、总则", 2), ("（一）范围", 3), ("3.1 应用系统", 3), ("五、经费", 2), ("（五）预算说明", 3)])
    assert unclear_nesting(whole) == []  # started at the first number, or the same numerals, or a dotted number
