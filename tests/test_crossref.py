"""Tests for figure/table caption resolution."""

from pathlib import Path

from parserx.assembly.crossref import CrossReferenceResolver
from parserx.assembly.markdown import MarkdownRenderer
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.pipeline import Pipeline


def test_resolver_attaches_figure_caption_and_skips_original_text():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(150, 310, 350, 330),
        content="图 1 系统架构图",
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption])])

    CrossReferenceResolver().resolve(doc)
    markdown = MarkdownRenderer().render(doc)

    assert image.metadata["caption"] == "图 1 系统架构图"
    assert caption.metadata["skip_render"] is True
    assert markdown.count("图 1 系统架构图") == 1
    assert "*图 1 系统架构图*" in markdown


def test_resolver_attaches_table_caption_above_table():
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(120, 80, 360, 100),
        content="表 2 关键参数",
    )
    table = PageElement(
        type="table",
        page_number=1,
        bbox=(100, 110, 420, 240),
        content="| 名称 | 值 |\n|---|---|\n| A | 1 |",
    )
    doc = Document(pages=[Page(number=1, elements=[caption, table])])

    CrossReferenceResolver().resolve(doc)
    markdown = MarkdownRenderer().render(doc)

    assert table.metadata["caption"] == "表 2 关键参数"
    assert caption.metadata["skip_render"] is True
    assert "表 2 关键参数\n\n| 名称 | 值 |" in markdown


def test_pipeline_renders_captioned_output(tmp_path: Path, monkeypatch):
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(150, 310, 360, 330),
        content="Figure 1. Pipeline overview",
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption])])

    cfg = ParserXConfig()
    cfg.builders.ocr.engine = "none"
    pipeline = Pipeline(cfg)

    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)
    monkeypatch.setattr(pipeline, "_extract_and_describe_images", lambda d, source, images_dir: d)

    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    pipeline.parse_to_dir(dummy, tmp_path)
    markdown = (tmp_path / "final.md").read_text(encoding="utf-8")

    assert "![](images/fig-1.png)" in markdown
    assert markdown.count("Figure 1. Pipeline overview") == 1
    assert "*Figure 1. Pipeline overview*" in markdown
