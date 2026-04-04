"""Tests for ChapterProcessor."""

import os
from pathlib import Path

import pytest

from parserx.builders.metadata import MetadataBuilder
from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.chapter import ChapterProcessor


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
