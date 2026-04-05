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


def test_parse_runs_image_extraction_without_output_dir(tmp_path: Path, monkeypatch):
    """parse() and parse_to_document() must still extract images + run VLM
    even though no output_dir is provided (using a temp dir internally)."""
    from parserx.models.elements import Document, Page, PageElement

    # Build a tiny document with one informational image
    img_elem = PageElement(
        type="image",
        bbox=(0, 0, 400, 300),
        metadata={"width": 400, "height": 300},
    )
    doc = Document(pages=[Page(number=1, width=595, height=842, elements=[img_elem])])

    pipeline = _pipeline_no_ocr()

    # Patch _extract to return our synthetic doc (avoid real PDF parsing)
    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)

    # Track _extract_and_describe_images calls while stubbing
    # the actual image extractor (no real PDF to extract from)
    call_log: list[str] = []
    _real_method = pipeline._extract_and_describe_images

    def tracking_stub(d, source, images_dir):
        call_log.append(str(images_dir))
        # Simulate what the real extractor would do: set saved_path
        for elem in d.all_elements:
            if elem.type == "image" and not elem.metadata.get("skipped"):
                elem.metadata["saved_path"] = "images/fake.png"
                elem.metadata["saved_abs_path"] = str(images_dir / "images" / "fake.png")
        return d

    monkeypatch.setattr(pipeline, "_extract_and_describe_images", tracking_stub)

    # Create a dummy PDF so path validation passes
    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    result_doc = pipeline.parse_to_document(dummy)

    # _extract_and_describe_images must have been called
    assert len(call_log) == 1
    # The temp dir should have been cleaned up — path no longer exists
    assert not Path(call_log[0]).exists()
    # Image element must NOT have stale saved_path / saved_abs_path
    img = result_doc.all_elements[0]
    assert "saved_path" not in img.metadata
    assert "saved_abs_path" not in img.metadata


def test_parse_unsupported_format(tmp_path: Path):
    fake = tmp_path / "test.xyz"
    fake.write_text("hello")
    pipeline = _pipeline_no_ocr()
    with pytest.raises(ValueError, match="Unsupported format"):
        pipeline.parse(fake)
