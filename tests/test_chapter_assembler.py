"""Tests for ChapterAssembler."""

from pathlib import Path

import pytest

from parserx.assembly.chapter import ChapterAssembler
from parserx.builders.metadata import MetadataBuilder
from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.chapter import ChapterProcessor


def _text_elem(content: str, font_size: float = 10.0, bold: bool = False) -> PageElement:
    return PageElement(
        type="text",
        content=content,
        font=FontInfo(name="SimSun", size=font_size, bold=bold),
    )


def _build_processed_doc() -> Document:
    """Build a document with headings already detected."""
    elements = [
        _text_elem("正文" * 50, 10.0),
        _text_elem("第一章 总则", 16.0, bold=True),
        _text_elem("这是第一章的内容。" * 10, 10.0),
        _text_elem("一、基本原则", 12.0, bold=True),
        _text_elem("基本原则的具体内容。" * 10, 10.0),
        _text_elem("第二章 技术要求", 16.0, bold=True),
        _text_elem("这是第二章的内容。" * 10, 10.0),
        _text_elem("一、材料规格", 12.0, bold=True),
        _text_elem("材料规格的具体要求。" * 10, 10.0),
    ]
    doc = Document(pages=[Page(number=1, width=595, height=842, elements=elements)])
    MetadataBuilder().build(doc)
    ChapterProcessor().process(doc)
    return doc


def test_assemble_creates_files(tmp_path: Path):
    doc = _build_processed_doc()
    assembler = ChapterAssembler()
    final_path = assembler.assemble(doc, tmp_path)

    assert final_path.exists()
    assert (tmp_path / "final.md").exists()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "chapters").is_dir()

    # Should have chapters
    chapter_files = sorted((tmp_path / "chapters").glob("ch_*.md"))
    assert len(chapter_files) >= 2  # At least 2 chapters (第一章, 第二章)


def test_index_contains_headings(tmp_path: Path):
    doc = _build_processed_doc()
    assembler = ChapterAssembler()
    assembler.assemble(doc, tmp_path)

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "目录" in index
    assert "第一章" in index or "总则" in index
    assert "第二章" in index or "技术要求" in index


def test_chapter_content(tmp_path: Path):
    doc = _build_processed_doc()
    assembler = ChapterAssembler()
    assembler.assemble(doc, tmp_path)

    # Each chapter file should contain its content
    chapters_dir = tmp_path / "chapters"
    all_text = ""
    for f in sorted(chapters_dir.glob("ch_*.md")):
        all_text += f.read_text(encoding="utf-8")

    assert "第一章" in all_text or "总则" in all_text
    assert "第二章" in all_text or "技术要求" in all_text


def test_no_split_when_disabled(tmp_path: Path):
    from parserx.config.schema import OutputConfig
    doc = _build_processed_doc()
    assembler = ChapterAssembler(OutputConfig(chapter_split=False))
    assembler.assemble(doc, tmp_path)

    assert (tmp_path / "final.md").exists()
    assert not (tmp_path / "chapters").exists()


# ── Integration with real PDF ───────────────────────────────────────────

SAMPLE_DIR = Path("/Users/xuyun/IEC/doc_special/sample_docs")
PDF_TEXT = SAMPLE_DIR / "pdf_text01.pdf"


@pytest.mark.skipif(not PDF_TEXT.exists(), reason="Test PDF not available")
def test_real_pdf_chapter_split(tmp_path: Path):
    """End-to-end: parse real PDF and split into chapters."""
    from parserx.pipeline import Pipeline

    pipeline = Pipeline()
    final_path = pipeline.parse_to_dir(PDF_TEXT, tmp_path)

    assert final_path.exists()
    assert (tmp_path / "index.md").exists()

    chapters = sorted((tmp_path / "chapters").glob("ch_*.md"))
    assert len(chapters) >= 3  # Should have multiple chapters

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "第一章" in index
