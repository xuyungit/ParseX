"""Tests for LineUnwrapProcessor."""

from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.line_unwrap import (
    LineUnwrapProcessor,
    _merge_adjacent_elements,
    _should_merge_elements,
    _unwrap_text_block,
)


def _doc_with_text(content: str, *, heading_level: int | None = None) -> Document:
    metadata = {}
    if heading_level is not None:
        metadata["heading_level"] = heading_level

    body_font = FontInfo(name="SimSun", size=12.0)
    element = PageElement(
        type="text",
        content=content,
        bbox=(0, 0, 100, 100),
        page_number=1,
        font=body_font,
        metadata=metadata,
    )
    doc = Document(pages=[Page(number=1, width=595, height=842, elements=[element])])
    doc.metadata.font_stats.body_font = body_font
    return doc


def test_unwrap_chinese_visual_breaks():
    text = "这是一个用于测试中文换行修复能力的较长段落第一行\n这里继续同一个句子的后半部分"
    assert (
        _unwrap_text_block(text, average_line_length=25)
        == "这是一个用于测试中文换行修复能力的较长段落第一行这里继续同一个句子的后半部分"
    )


def test_preserve_chinese_sentence_boundary():
    text = "这是第一段的结尾。\n这是第二段的开头"
    assert _unwrap_text_block(text, average_line_length=25) == text


def test_unwrap_english_lowercase_continuation():
    text = "This is the first half of a sentence\nand this is the continuation."
    assert (
        _unwrap_text_block(text, average_line_length=30)
        == "This is the first half of a sentence and this is the continuation."
    )


def test_unwrap_hyphenated_word():
    text = "multi-\nline parsing"
    assert _unwrap_text_block(text, average_line_length=20) == "multiline parsing"


def test_preserve_list_items():
    text = "1. 第一项内容\n2. 第二项内容"
    assert _unwrap_text_block(text, average_line_length=20) == text


def test_preserve_chinese_list_markers():
    text = "一、总则\n二、适用范围\n第一条 为了规范管理"
    assert _unwrap_text_block(text, average_line_length=20) == text


def test_preserve_blank_line_between_paragraphs():
    text = "第一段前半句\n第一段后半句\n\n第二段"
    assert _unwrap_text_block(text, average_line_length=20) == "第一段前半句\n第一段后半句\n\n第二段"


def test_skip_heading_elements():
    doc = _doc_with_text("第一章\n项目概况", heading_level=1)
    result = LineUnwrapProcessor().process(doc)
    assert result.pages[0].elements[0].content == "第一章\n项目概况"


def test_pipeline_style_processing_updates_text_elements():
    doc = _doc_with_text(
        "这是一个足够长的正文段落第一行用于估算长度\n第二行继续补充说明内容"
    )
    result = LineUnwrapProcessor().process(doc)
    assert result.pages[0].elements[0].content == (
        "这是一个足够长的正文段落第一行用于估算长度第二行继续补充说明内容"
    )


# ── Cross-element merging tests ──────────────────────────────────────

_BODY = FontInfo(name="SimSun", size=12.0)
_HEADING_FONT = FontInfo(name="SimSun", size=18.0, bold=True)


def _elem(text: str, *, font: FontInfo = _BODY, bbox=(0, 0, 100, 20),
          page: int = 1, heading: int | None = None, **kw) -> PageElement:
    meta = dict(kw)
    if heading is not None:
        meta["heading_level"] = heading
    return PageElement(type="text", content=text, bbox=bbox, page_number=page,
                       font=font, metadata=meta)


def test_cross_element_cjk_merge():
    """Two CJK elements where the first line is long enough → merge."""
    a = _elem("本年度安全：小的安全事故，钢板毛刺多，抱钢板时毛刺刺进手指里了。后面钢",
              bbox=(50, 100, 500, 120))
    b = _elem("板处理那边也在处理毛刺了。",
              bbox=(50, 120, 300, 140))
    result = _merge_adjacent_elements([a, b], average_line_length=30, typical_gap=5.0)
    assert len(result) == 1
    assert "后面钢板处理那边也在处理毛刺了。" in result[0].content


def test_cross_element_english_lowercase():
    """English continuation with lowercase start → merge with space."""
    a = _elem("This is the first part of a very long sentence that",
              bbox=(50, 100, 500, 120))
    b = _elem("continues on the next line.",
              bbox=(50, 120, 300, 140))
    result = _merge_adjacent_elements([a, b], average_line_length=40, typical_gap=5.0)
    assert len(result) == 1
    assert result[0].content == (
        "This is the first part of a very long sentence that continues on the next line."
    )


def test_no_merge_different_font():
    a = _elem("正文内容第一行比较长的文字测试", font=_BODY)
    b = _elem("脚注内容", font=FontInfo(name="SimSun", size=9.0))
    result = _merge_adjacent_elements([a, b], average_line_length=20, typical_gap=None)
    assert len(result) == 2


def test_no_merge_sentence_end():
    """First line ends with period → no merge (paragraph boundary)."""
    a = _elem("这是完整的第一段。")
    b = _elem("这是第二段的开始")
    result = _merge_adjacent_elements([a, b], average_line_length=20, typical_gap=None)
    assert len(result) == 2


def test_no_merge_heading():
    a = _elem("正文内容在这里比较长的一行文字")
    b = _elem("第二章 新的章节", heading=2)
    result = _merge_adjacent_elements([a, b], average_line_length=20, typical_gap=None)
    assert len(result) == 2


def test_no_merge_next_is_list_item():
    a = _elem("正文内容在这里比较长的一行文字")
    b = _elem("1、第一条规定内容")
    result = _merge_adjacent_elements([a, b], average_line_length=20, typical_gap=None)
    assert len(result) == 2


def test_list_item_merges_continuation():
    """List item current line + continuation → should merge."""
    a = _elem("3、支座外观缺陷问题比较严重，一部分是模具痕迹导致的，一部分是产品生产",
              bbox=(50, 100, 500, 120))
    b = _elem("出来稀胶，裂口比较多。",
              bbox=(50, 120, 300, 140))
    result = _merge_adjacent_elements([a, b], average_line_length=30, typical_gap=5.0)
    assert len(result) == 1
    assert "产品生产出来稀胶" in result[0].content


def test_no_merge_large_gap():
    """Large vertical gap → paragraph break, no merge."""
    a = _elem("正文内容在这里比较长的一行文字测试用的内容",
              bbox=(50, 100, 500, 120))
    b = _elem("另一段的开始内容",
              bbox=(50, 160, 500, 180))
    # typical_gap=5, actual gap=40 → 40 > 5*2 → no merge
    result = _merge_adjacent_elements([a, b], average_line_length=20, typical_gap=5.0)
    assert len(result) == 2


def test_chain_merge_three_elements():
    """Three continuation lines → collapse into one."""
    a = _elem("第一行内容比较长的文字继续写下去不断补充", bbox=(50, 100, 500, 120))
    b = _elem("第二行继续承接上文的内容还要继续", bbox=(50, 120, 500, 140))
    c = _elem("第三行最后的部分。", bbox=(50, 140, 300, 160))
    result = _merge_adjacent_elements([a, b, c], average_line_length=15, typical_gap=5.0)
    assert len(result) == 1
    assert "继续写下去不断补充第二行" in result[0].content


def test_non_text_element_as_merge_barrier():
    """Image element between text → prevents merging across it."""
    a = _elem("正文内容在这里比较长的一行文字")
    img = PageElement(type="image", content="", bbox=(50, 120, 500, 300), page_number=1)
    b = _elem("继续的正文内容")
    result = _merge_adjacent_elements([a, img, b], average_line_length=20, typical_gap=None)
    assert len(result) == 3


def test_bbox_updated_after_merge():
    a = _elem("这是一个足够长的中文段落第一行文字", bbox=(50, 100, 500, 120))
    b = _elem("继续第二行内容在这里补充一些更长的文字", bbox=(30, 120, 480, 140))
    result = _merge_adjacent_elements([a, b], average_line_length=15, typical_gap=5.0)
    assert len(result) == 1
    assert result[0].bbox == (30, 100, 500, 140)
