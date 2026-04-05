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
    doc = _build_doc([
        _text_elem("这是正文内容" * 20, 10.0),
        _text_elem("1. 项目概况", 10.0, bold=False),
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
    doc = _build_doc([
        _text_elem("这是正文内容" * 20, 10.0),
        _text_elem("1. 项目概况", 10.0, bold=False),
        _text_elem("1.1 适用范围", 10.0, bold=False),
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
        _text_elem("1. 项目概况", 10.0, bold=False),
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
