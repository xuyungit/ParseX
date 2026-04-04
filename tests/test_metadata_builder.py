"""Tests for MetadataBuilder."""

from parserx.builders.metadata import MetadataBuilder, detect_numbering_signal
from parserx.models.elements import Document, FontInfo, Page, PageElement


def _make_text_element(content: str, font_size: float = 10.0, bold: bool = False, page: int = 1) -> PageElement:
    return PageElement(
        type="text",
        content=content,
        page_number=page,
        font=FontInfo(name="SimSun", size=font_size, bold=bold),
    )


def test_detect_numbering_chapter_cn():
    assert detect_numbering_signal("第一章 总则") == ("chapter_cn", "H1")
    assert detect_numbering_signal("第3节 技术要求") == ("chapter_cn", "H1")


def test_detect_numbering_section_cn():
    assert detect_numbering_signal("一、项目概况") == ("section_cn", "H2")
    assert detect_numbering_signal("三.采购内容") == ("section_cn", "H2")


def test_detect_numbering_section_cn_paren():
    assert detect_numbering_signal("（一）基本要求") == ("section_cn_paren", "H2")
    assert detect_numbering_signal("(二)技术��数") == ("section_cn_paren", "H2")


def test_detect_numbering_arabic_nested():
    assert detect_numbering_signal("3.1.2 材料规格") == ("section_arabic_nested", "H3")
    assert detect_numbering_signal("1.1 概述") == ("section_arabic_nested", "H3")


def test_detect_numbering_none():
    assert detect_numbering_signal("这是一段正文内容。") is None
    assert detect_numbering_signal("") is None


def test_body_font_detection():
    """Body font should be the most commonly used font."""
    elements = [
        _make_text_element("正文内容" * 20, font_size=10.0),  # Body: lots of text
        _make_text_element("正文内容" * 20, font_size=10.0),
        _make_text_element("标题", font_size=16.0, bold=True),  # Heading: little text
    ]
    doc = Document(pages=[Page(number=1, elements=elements)])
    builder = MetadataBuilder()
    builder.build(doc)

    assert doc.metadata.font_stats.body_font.size == 10.0
    assert doc.metadata.font_stats.body_font.bold is False


def test_heading_candidates():
    """Fonts larger than body should be heading candidates."""
    elements = [
        _make_text_element("正文" * 50, font_size=10.0),
        _make_text_element("大标题", font_size=18.0, bold=True),
        _make_text_element("中标题", font_size=14.0, bold=True),
        _make_text_element("小标题", font_size=12.0, bold=True),
    ]
    doc = Document(pages=[Page(number=1, elements=elements)])
    builder = MetadataBuilder()
    builder.build(doc)

    candidates = doc.metadata.font_stats.heading_candidates
    assert len(candidates) >= 2
    # First candidate should be the largest font
    assert candidates[0].size == 18.0


def test_numbering_pattern_detection():
    """Should detect numbering patterns across the document."""
    elements = [
        _make_text_element("第一章 总则", font_size=14.0),
        _make_text_element("正文内容" * 20, font_size=10.0),
        _make_text_element("第二章 技术要求", font_size=14.0),
        _make_text_element("正文内容" * 20, font_size=10.0),
        _make_text_element("1.1 概述", font_size=12.0),
        _make_text_element("1.2 范围", font_size=12.0),
    ]
    doc = Document(pages=[Page(number=1, elements=elements)])
    builder = MetadataBuilder()
    builder.build(doc)

    signals = {p.signal for p in doc.metadata.numbering_patterns}
    assert "chapter_cn" in signals
    assert "section_arabic_nested" in signals
