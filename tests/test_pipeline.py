"""Tests for the pipeline with real PDF files."""

import os
from pathlib import Path

import pytest

from parserx.config.schema import ParserXConfig
from parserx.pipeline import Pipeline


def _pipeline_no_ocr():
    """Create a Pipeline with OCR disabled (no credentials needed)."""
    cfg = ParserXConfig()
    cfg.builders.ocr.engine = "none"
    return Pipeline(cfg)

# Sample docs — set PARSERX_SAMPLE_DIR env var to point to test PDFs
SAMPLE_DIR = Path(os.environ.get("PARSERX_SAMPLE_DIR", "sample_docs"))
PDF_TEXT = SAMPLE_DIR / "pdf_text01.pdf"
DEEPSEEK = SAMPLE_DIR / "deepseek.pdf"


@pytest.mark.skipif(not PDF_TEXT.exists(), reason="Test PDF not available")
def test_parse_simple_pdf():
    pipeline = Pipeline()
    result = pipeline.parse(PDF_TEXT)
    assert len(result) > 0
    assert "<!-- PAGE 1 -->" in result


@pytest.mark.skipif(not DEEPSEEK.exists(), reason="Test PDF not available")
def test_parse_deepseek_pdf():
    pipeline = Pipeline()
    doc = pipeline.parse_to_document(DEEPSEEK)
    assert len(doc.pages) > 0
    assert doc.metadata.source_format == "pdf"
    # Should have some text elements
    text_elements = doc.elements_by_type("text")
    assert len(text_elements) > 0


def test_parse_nonexistent():
    pipeline = _pipeline_no_ocr()
    with pytest.raises(FileNotFoundError):
        pipeline.parse("/nonexistent/file.pdf")


def test_parse_unsupported_format(tmp_path: Path):
    fake = tmp_path / "test.xyz"
    fake.write_text("hello")
    pipeline = _pipeline_no_ocr()
    with pytest.raises(ValueError, match="Unsupported format"):
        pipeline.parse(fake)
