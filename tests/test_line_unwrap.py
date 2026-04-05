"""Tests for LineUnwrapProcessor."""

from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.line_unwrap import (
    LineUnwrapProcessor,
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
