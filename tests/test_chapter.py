"""Tests for ChapterProcessor."""

import os
from pathlib import Path

import pytest

from parserx.builders.metadata import MetadataBuilder
from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.chapter import ChapterProcessor


class FakeLLMService:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        self.calls.append((system, user))
        return self.response


def _text_elem(content: str, font_size: float = 10.0, bold: bool = False) -> PageElement:
    return PageElement(
        type="text",
        content=content,
        font=FontInfo(name="SimSun", size=font_size, bold=bold),
    )


def _build_doc(elements: list[PageElement]) -> Document:
    """Build a document, run MetadataBuilder, return it."""
    doc = Document(pages=[Page(number=1, elements=elements)])
    MetadataBuilder().build(doc)
    return doc


def test_detect_chapter_cn():
    """Chinese chapter numbering should be detected as H1."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("第一章 总则", 14.0, bold=True),
        _text_elem("正文" * 50, 10.0),
    ])
    processor = ChapterProcessor()
    processor.process(doc)

    heading = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(heading) == 1
    assert heading[0].metadata["heading_level"] == 1


def test_detect_section_cn():
    """Chinese section numbering should be detected."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("一、项目概况", 12.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("二、采购需求", 12.0, bold=True),
        _text_elem("正文" * 50, 10.0),
    ])
    processor = ChapterProcessor()
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(headings) == 2
    assert all(h.metadata["heading_level"] == 2 for h in headings)


def test_detect_arabic_nested():
    """Nested Arabic numbering (1.1, 1.2) should be H3."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("1.1 概述", 11.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("1.2 范围", 11.0, bold=True),
    ])
    processor = ChapterProcessor()
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(headings) == 2
    assert all(h.metadata["heading_level"] == 3 for h in headings)


def test_font_only_heading():
    """Large bold text without numbering should still be detected as heading."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("技术规格书", 18.0, bold=True),
        _text_elem("正文" * 50, 10.0),
    ])
    processor = ChapterProcessor()
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(headings) == 1


def test_body_text_not_detected():
    """Long body text should never be detected as heading."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("这是一段很长的正文内容，包含了各种各样的信息和描述，不应该被识别为标题。", 10.0),
    ])
    processor = ChapterProcessor()
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(headings) == 0


def test_disabled():
    """Processor should be a no-op when disabled."""
    from parserx.config.schema import ProcessorToggle
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("第一章 总则", 14.0, bold=True),
    ])
    processor = ChapterProcessor(ProcessorToggle(enabled=False))
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("heading_level")]
    assert len(headings) == 0


def test_llm_fallback_confirms_weak_numbering_candidate():
    # Use font.size=0 (OCR default) for the heading candidate — "N." format
    # with real body font is too ambiguous and is now filtered before fallback.
    doc = _build_doc([
        _text_elem("这是正文内容" * 20, 10.0),
        _text_elem("1. 项目概况", 0.0, bold=False),
        _text_elem("这是后续正文内容" * 20, 10.0),
    ])
    llm = FakeLLMService('[{"idx": 1, "level": 2}]')

    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert target.metadata["heading_level"] == 2
    assert target.metadata["llm_fallback_used"] is True
    assert len(llm.calls) == 1
    assert doc.metadata.processing_stats["llm_calls"] == 1


def test_llm_fallback_tracks_api_calls_separately_from_hits():
    # Use font.size=0 (OCR default) for heading candidates —
    # "N." format with body font is filtered before LLM fallback.
    doc = _build_doc([
        _text_elem("这是正文内容" * 20, 10.0),
        _text_elem("1. 项目概况", 0.0, bold=False),
        _text_elem("1.1 适用范围", 0.0, bold=False),
        _text_elem("这是后续正文内容" * 20, 10.0),
    ])
    llm = FakeLLMService('[{"idx": 1, "level": 2}, {"idx": 2, "level": 3}]')

    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    headings = [e for e in doc.all_elements if e.metadata.get("llm_fallback_used")]
    assert len(headings) == 2
    assert len(llm.calls) == 1
    assert doc.metadata.processing_stats["llm_calls"] == 1


def test_llm_fallback_ignores_invalid_json():
    doc = _build_doc([
        _text_elem("这是正文内容" * 20, 10.0),
        _text_elem("1. 项目概况", 0.0, bold=False),
    ])
    llm = FakeLLMService("not-json")

    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert "heading_level" not in target.metadata
    assert len(llm.calls) == 1
    assert doc.metadata.processing_stats["llm_calls"] == 1


def test_normalize_ocr_title_subtitle_pair_demotes_doc_title_h1():
    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(
                        type="text",
                        content="金诚信（603979）：矿服业务强增长，资源业务扩成长",
                        source="ocr",
                        layout_type="doc_title",
                        metadata={"heading_level": 1},
                    ),
                    PageElement(
                        type="text",
                        content="——金诚信（603979）2022年报点评",
                        source="ocr",
                        layout_type="paragraph_title",
                        metadata={"heading_level": 2},
                    ),
                ],
            )
        ]
    )

    ChapterProcessor().process(doc)

    assert doc.pages[0].elements[0].metadata["heading_level"] == 2
    assert doc.pages[0].elements[0].metadata["ocr_heading_level_adjusted"] == "title_subtitle_pair"


def test_sidebar_short_ocr_label_is_suppressed_as_heading():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=2000,
                elements=[
                    PageElement(
                        type="text",
                        content="交易数据",
                        source="ocr",
                        layout_type="paragraph_title",
                        bbox=(1500, 100, 1700, 160),
                        metadata={"heading_level": 2},
                    ),
                ],
            )
        ]
    )

    ChapterProcessor().process(doc)

    assert "heading_level" not in doc.pages[0].elements[0].metadata
    assert doc.pages[0].elements[0].metadata["ocr_heading_suppressed"] == "sidebar_short_label"


def test_sidebar_numeric_label_with_weak_spaced_numbering_is_suppressed():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=1000,
                elements=[
                    PageElement(
                        type="text",
                        content="52 周股价走势图",
                        source="ocr",
                        layout_type="paragraph_title",
                        bbox=(700, 100, 920, 150),
                        metadata={"heading_level": 2},
                    ),
                ],
            )
        ]
    )

    ChapterProcessor().process(doc)

    assert "heading_level" not in doc.pages[0].elements[0].metadata
    assert doc.pages[0].elements[0].metadata["ocr_heading_suppressed"] == "sidebar_short_label"


def test_sidebar_colon_label_is_promoted_to_heading():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=2000,
                elements=[
                    PageElement(
                        type="text",
                        content="未来3-6个月重大事项提示：",
                        source="ocr",
                        layout_type="text",
                        bbox=(1500, 100, 1850, 160),
                    ),
                ],
            )
        ]
    )

    ChapterProcessor().process(doc)

    assert doc.pages[0].elements[0].metadata["heading_level"] == 2


def test_merge_cover_heading_fragments():
    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(
                        type="text",
                        content="基于大模型的城轨工务专业知识问答助手",
                        bbox=(100, 80, 900, 120),
                        metadata={"heading_level": 1},
                    ),
                    PageElement(
                        type="text",
                        content="技术研究及应用项目中期验收评审意见",
                        bbox=(100, 135, 900, 175),
                        metadata={"heading_level": 1},
                    ),
                    PageElement(
                        type="text",
                        content="2025年9月30日，北京市地铁运营有限公司在北京组织召开了项目评审。",
                        bbox=(100, 260, 1100, 340),
                    ),
                ],
            )
        ]
    )

    ChapterProcessor().process(doc)

    first, second = doc.pages[0].elements[:2]
    assert first.content == "基于大模型的城轨工务专业知识问答助手技术研究及应用项目中期验收评审意见"
    assert second.metadata["skip_render"] is True


# ── Integration test with real PDF ──────────────────────────────────────

SAMPLE_DIR = Path(os.environ.get("PARSERX_SAMPLE_DIR", "sample_docs"))
PDF_TEXT = SAMPLE_DIR / "pdf_text01.pdf"


@pytest.mark.skipif(not PDF_TEXT.exists(), reason="Test PDF not available")
def test_real_pdf_chapter_detection():
    """End-to-end: parse real PDF and verify headings are detected."""
    from parserx.config.schema import ParserXConfig
    from parserx.pipeline import Pipeline

    config = ParserXConfig()
    # Skip if OCR service credentials are not configured
    ocr_cfg = config.builders.ocr
    if ocr_cfg.engine != "none" and (not ocr_cfg.endpoint or not ocr_cfg.token):
        pytest.skip("OCR credentials not configured")

    pipeline = Pipeline(config)
    result = pipeline.parse(PDF_TEXT)

    # The procurement doc should have chapter headings (第X章)
    assert "# " in result or "## " in result, "Expected heading markers in output"


# ── Numbering coherence tests ─────────────────────────────────────────


def test_coherence_sequential_arabic_root():
    """Sequential arabic root numbers (0-6) should be promoted to H2 without font signal."""
    body = "正文内容" * 30
    elements = [_text_elem(body)]
    for i in range(7):
        elements.append(_text_elem(f"{i} 第{i}节内容标题"))
        elements.append(_text_elem(body))

    doc = _build_doc(elements)
    ChapterProcessor().process(doc)

    headings = [e for e in doc.pages[0].elements if e.metadata.get("heading_level")]
    assert len(headings) == 7
    for h in headings:
        assert h.metadata["heading_level"] == 2
        assert h.metadata.get("numbering_coherence") is True


def test_coherence_nested_subsections():
    """Nested subsections (2.1, 2.2, 3.1, 3.2, 3.3) should be promoted to H3."""
    body = "正文内容" * 30
    elements = [_text_elem(body)]
    for label in ["2.1 损伤情形一", "2.2 损伤情形二", "3.1 分析方法", "3.2 实验结果", "3.3 讨论"]:
        elements.append(_text_elem(label))
        elements.append(_text_elem(body))

    doc = _build_doc(elements)
    ChapterProcessor().process(doc)

    headings = [e for e in doc.pages[0].elements if e.metadata.get("heading_level")]
    assert len(headings) == 5
    for h in headings:
        assert h.metadata["heading_level"] == 3
        assert h.metadata.get("numbering_coherence") is True


def test_coherence_not_triggered_for_isolated_numbers():
    """Isolated arabic numbers (non-sequential) should NOT be promoted."""
    body = "正文内容" * 30
    elements = [
        _text_elem(body),
        _text_elem("1 某段落开头"),
        _text_elem(body),
        _text_elem("5 另一段落开头"),
        _text_elem(body),
    ]

    doc = _build_doc(elements)
    ChapterProcessor().process(doc)

    headings = [e for e in doc.pages[0].elements if e.metadata.get("heading_level")]
    assert len(headings) == 0


def test_coherence_coexists_with_strong_signals():
    """Chinese chapter headings (strong) and arabic coherence should both work."""
    body = "正文内容" * 30
    elements = [
        _text_elem(body),
        _text_elem("第一章 总则", 14.0, bold=True),
        _text_elem(body),
        _text_elem("1 范围"),
        _text_elem(body),
        _text_elem("2 术语"),
        _text_elem(body),
        _text_elem("3 材料"),
        _text_elem(body),
    ]

    doc = _build_doc(elements)
    ChapterProcessor().process(doc)

    headings = [e for e in doc.pages[0].elements if e.metadata.get("heading_level")]
    assert len(headings) == 4  # 1 chapter_cn + 3 coherence


def test_is_coherent_sequence():
    """Unit test for _is_coherent_sequence helper."""
    from parserx.processors.chapter import _is_coherent_sequence

    assert _is_coherent_sequence([0, 1, 2, 3, 4, 5, 6]) is True
    assert _is_coherent_sequence([1, 2, 3]) is True
    assert _is_coherent_sequence([0, 1, 3, 4, 5]) is True  # gap of 2 ok
    assert _is_coherent_sequence([1, 5]) is False  # too few
    assert _is_coherent_sequence([1, 2]) is False  # min_count=3
    assert _is_coherent_sequence([1, 2], min_count=2) is True
    assert _is_coherent_sequence([1, 4, 8]) is False  # gaps too large


def test_section_arabic_spaced_includes_zero():
    """Regex should match section 0 (e.g., '0 引 言')."""
    from parserx.builders.metadata import detect_numbering_signal

    result = detect_numbering_signal("0 引 言")
    assert result is not None
    assert result[0] == "section_arabic_spaced"


# ── Multiline heading number resolution ──────────────────────────────


def test_resolve_heading_text_joins_number_and_title():
    """'5\\n算例分析' should be resolved to '5 算例分析' and detected as heading."""
    from parserx.processors.chapter import _resolve_heading_text

    assert _resolve_heading_text("5\n算例分析") == "5 算例分析"
    assert _resolve_heading_text("6\n结语") == "6 结语"
    # Multi-digit
    assert _resolve_heading_text("12\nConclusion") == "12 Conclusion"


def test_resolve_heading_text_ignores_body_second_line():
    """Pure number followed by long body text should NOT be joined."""
    from parserx.processors.chapter import _resolve_heading_text

    # Body text on second line — should return just the number
    long_body = "5\n某两跨连续梁桥的跨度为2×50m，桥面宽度为12.5m，横向设置5片T梁。"
    assert _resolve_heading_text(long_body) == "5"


def test_resolve_heading_text_passthrough_normal():
    """Non-pure-number first lines should be returned as-is."""
    from parserx.processors.chapter import _resolve_heading_text

    assert _resolve_heading_text("3.2 方法") == "3.2 方法"
    assert _resolve_heading_text("第一章 总则") == "第一章 总则"
    assert _resolve_heading_text("Introduction") == "Introduction"


def test_multiline_number_detected_as_heading():
    """Element with '5\\n算例分析' at heading font should be H2."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("1\n引言", 13.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("2\n方法", 13.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("3\n结果", 13.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("4\n讨论", 13.0, bold=True),
        _text_elem("正文" * 50, 10.0),
        _text_elem("5\n算例分析", 13.0, bold=True),
        _text_elem("正文" * 50, 10.0),
    ])
    proc = ChapterProcessor()
    doc = proc.process(doc)

    headings = [
        e for e in doc.pages[0].elements
        if e.metadata.get("heading_level")
    ]
    # All 5 numbered sections should be detected
    assert len(headings) >= 5
    # Section 5 specifically
    sec5 = [e for e in headings if "算例分析" in e.content]
    assert len(sec5) == 1
    assert sec5[0].metadata["heading_level"] == 2


# ── Zero-signal fallback tests ────────────────────────────────────────


def _ocr_elem(content: str) -> PageElement:
    """Create an OCR-sourced element with default font (size=0)."""
    return PageElement(
        type="text",
        content=content,
        font=FontInfo(),
        source="ocr",
    )


def test_zero_signal_short_text_enters_llm_fallback():
    """Short unnumbered OCR text with no font signal should reach LLM fallback."""
    doc = Document(pages=[Page(number=1, elements=[
        _ocr_elem("正文" * 50),
        _ocr_elem("前言"),  # no numbering, no font info (OCR default)
        _ocr_elem("正文内容后续" * 30),
    ])])
    MetadataBuilder().build(doc)
    llm = FakeLLMService('[{"idx": 1, "level": 2}]')
    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert target.metadata.get("heading_level") == 2
    assert target.metadata.get("llm_fallback_used") is True
    assert len(llm.calls) == 1


def test_zero_signal_long_text_rejected():
    """OCR text longer than 30 chars with no signal should NOT enter fallback."""
    doc = Document(pages=[Page(number=1, elements=[
        _ocr_elem("正文" * 50),
        _ocr_elem("这是一段比较长的文本它不是标题而是正文内容描述用来测试需要超过三十个字符才行"),
        _ocr_elem("正文内容后续" * 30),
    ])])
    MetadataBuilder().build(doc)
    llm = FakeLLMService('[{"idx": 1, "level": 2}]')
    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert "heading_level" not in target.metadata


def test_zero_signal_colon_ending_rejected():
    """Zero-signal OCR text ending with colon should NOT enter fallback."""
    doc = Document(pages=[Page(number=1, elements=[
        _ocr_elem("正文" * 50),
        _ocr_elem("编制单位："),
        _ocr_elem("正文内容后续" * 30),
    ])])
    MetadataBuilder().build(doc)
    llm = FakeLLMService('[{"idx": 1, "level": 2}]')
    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert "heading_level" not in target.metadata
    assert len(llm.calls) == 0


def test_zero_signal_native_pdf_rejected():
    """Native PDF elements with real font info (size > 0) should NOT enter zero-signal fallback."""
    doc = _build_doc([
        _text_elem("正文" * 50, 10.0),
        _text_elem("前言", 10.0, bold=False),  # has font info → not zero-signal eligible
        _text_elem("正文内容后续" * 30, 10.0),
    ])
    llm = FakeLLMService('[{"idx": 1, "level": 2}]')
    processor = ChapterProcessor(llm_service=llm)
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert "heading_level" not in target.metadata


def test_zero_signal_no_llm_graceful():
    """Without LLM, zero-signal headings are simply missed (no crash)."""
    doc = Document(pages=[Page(number=1, elements=[
        _ocr_elem("正文" * 50),
        _ocr_elem("前言"),
        _ocr_elem("正文内容后续" * 30),
    ])])
    MetadataBuilder().build(doc)
    processor = ChapterProcessor()
    processor.process(doc)

    target = doc.pages[0].elements[1]
    assert "heading_level" not in target.metadata
