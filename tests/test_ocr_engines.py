"""OCR engine comparison (docs/v2_ocr_engines.md): the shared page images, the image-only PDF, page engines and
the local worker protocol — offline, with a fake worker."""

import json
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from parserx.tool_eval import ocr_engines
from parserx.tool_eval.adapters import ToolAdapter, ToolRun
from parserx.tool_eval.ocr_engines import ImagesOnly, LocalWorker, PageEngine, image_pdf, page_images, scan_dpi


def _text_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=595, height=842).insert_text((72, 72), f"page {n + 1}")
    doc.save(str(path))
    return path


def _scan_pdf(path: Path, px: tuple[int, int] = (1240, 1754), rotate: int = 0) -> Path:
    """One A4 page holding one full-page image of *px* pixels (150 dpi by default)."""
    image = path.with_suffix(".png")
    Image.new("RGB", px, "white").save(image)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(image))
    page.set_rotation(rotate)
    doc.save(str(path))
    return path


def test_text_pages_render_at_the_fixed_resolution(tmp_path):
    images = page_images(_text_pdf(tmp_path / "a.pdf"), tmp_path / "in")
    assert [i.path.name for i in images] == ["p001.png", "p002.png"]
    assert {i.dpi for i in images} == {ocr_engines.RENDER_DPI}
    with Image.open(images[0].path) as im:
        assert im.width == round(595 / 72 * ocr_engines.RENDER_DPI)


def test_a_scan_keeps_its_own_resolution_also_on_a_turned_page(tmp_path):
    with pymupdf.open(_scan_pdf(tmp_path / "s.pdf")) as doc:
        assert scan_dpi(doc[0]) == 150
    with pymupdf.open(_scan_pdf(tmp_path / "r.pdf", rotate=90)) as doc:
        assert scan_dpi(doc[0]) == 150
    with pymupdf.open(_scan_pdf(tmp_path / "fine.pdf", px=(4960, 7016))) as doc:  # 600 dpi: capped
        assert scan_dpi(doc[0]) == ocr_engines.MAX_SCAN_DPI


def test_image_pdf_has_no_text_and_the_page_sizes(tmp_path):
    images = page_images(_text_pdf(tmp_path / "a.pdf"), tmp_path / "in")
    with pymupdf.open(image_pdf(images, tmp_path / "in" / "pages.pdf")) as doc:
        assert doc.page_count == 2
        assert doc[0].get_text().strip() == ""
        assert round(doc[0].rect.width) == 595 and round(doc[0].rect.height) == 842


class _Inner(ToolAdapter):
    name, label = "inner", "Inner"

    def parse(self, input_path, out_dir):
        self.seen = input_path
        return ToolRun(markdown="x")


def test_images_only_hands_the_inner_tool_the_image_pdf(tmp_path):
    inner = _Inner()
    out = tmp_path / "results" / "tool" / "doc"
    out.mkdir(parents=True)
    run = ImagesOnly(inner, "renamed").parse(_text_pdf(tmp_path / "a.pdf"), out)
    assert inner.seen == tmp_path / "results" / "_inputs" / "doc" / "pages.pdf"
    assert run.notes and "图片" in run.notes[0]


class _Pages(PageEngine):
    name, label, config = "fake", "Fake", {"k": 1}

    def read(self, image):
        return {"markdown": "" if image.name == "p002.png" else f"text of {image.name}", "seconds": 0.5,
                "cost_usd": 0.001}


def test_page_engine_joins_pages_in_order_and_keeps_each_answer(tmp_path):
    out = tmp_path / "results" / "fake" / "doc"
    out.mkdir(parents=True)
    run = _Pages().parse(_text_pdf(tmp_path / "a.pdf", pages=3), out)
    assert run.markdown.index("text of p001.png") < run.markdown.index("text of p003.png")
    assert "<!-- PAGE 2 -->" in run.markdown
    assert run.notes == ["第 2 页没有读出内容"]
    assert run.cost_usd == pytest.approx(0.003)
    assert json.loads((out / "raw" / "p003.json").read_text())["markdown"] == "text of p003.png"
    assert json.loads((out / "raw" / "pages.json").read_text())["seconds"] == [0.5, 0.5, 0.5]


_WORKER = """
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if request["image"].endswith("bad.png"):
        print(json.dumps({"error": "cannot read"}), flush=True)
    else:
        print(json.dumps({"markdown": "read " + request["image"], "seconds": 0.1}), flush=True)
"""


def test_local_worker_answers_line_by_line_and_reports_errors(tmp_path, monkeypatch):
    home, scripts = tmp_path / "engines", tmp_path / "scripts"
    (home / "fake" / ".venv" / "bin").mkdir(parents=True)
    (home / "fake" / ".venv" / "bin" / "python").symlink_to(sys.executable)
    scripts.mkdir()
    (scripts / "fake.py").write_text(_WORKER)
    monkeypatch.setattr(ocr_engines, "ENGINE_HOME", home)
    monkeypatch.setattr(ocr_engines, "WORKERS", scripts)
    worker = LocalWorker("fake", "fake.py")
    try:
        assert worker.ask({"image": "/a.png"})["markdown"] == "read /a.png"
        with pytest.raises(RuntimeError, match="cannot read"):
            worker.ask({"image": "/bad.png"})
        assert worker.ask({"image": "/b.png"})["markdown"] == "read /b.png"  # the same process goes on
    finally:
        worker.close()


def test_a_missing_engine_environment_is_named(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_engines, "ENGINE_HOME", tmp_path)
    with pytest.raises(RuntimeError, match="engine environment missing"):
        LocalWorker("absent", "x.py").ask({"image": "/a.png"})


def test_sent_image_is_a_jpeg_within_the_pixel_limit(tmp_path):
    big = tmp_path / "p001.png"
    Image.new("RGB", (4000, 3000), "white").save(big)
    sent = ocr_engines.sent_image(big, 1_000_000)
    with Image.open(sent) as im:
        assert im.format == "JPEG" and im.width * im.height <= 1_000_000
    small = tmp_path / "p002.png"
    Image.new("RGB", (100, 50), "white").save(small)
    assert ocr_engines.sent_image(small, 1_000_000) == small  # sent as it is, no copy made


def test_tidy_turns_a_written_out_line_break_in_a_cell_into_a_space_only():
    md = "a\\nb\n\n<table><tr><td>要点齐全\\n观点</td><td>$\\nu$</td></tr></table>"
    assert ocr_engines.tidy(md) == "a\\nb\n\n<table><tr><td>要点齐全 观点</td><td>$\\nu$</td></tr></table>"


def test_tidy_leaves_out_text_read_inside_a_picture():
    md = '前\n\n<div style="text-align: center;"><img src="imgs/a.jpg" alt="Image" />\n\n $ e_1 $\n☕\n\n</div>\n\n后'
    assert ocr_engines.tidy(md) == "前\n\n![](imgs/a.jpg)\n\n后"
    assert ocr_engines.tidy('<div style="text-align: center;">A</div>') == "A"


def test_dots_markdown_keeps_reading_text_and_drops_furniture_and_pictures():
    raw = json.dumps([{"category": "Page-header", "text": "期刊"}, {"category": "Title", "text": "题目"},
                      {"category": "Picture", "bbox": [0, 0, 1, 1]}, {"category": "Formula", "text": "a+b"},
                      {"category": "Table", "text": "<table><tr><td>1</td></tr></table>"},
                      {"category": "Text", "text": "正文"}], ensure_ascii=False)
    md, blocks = ocr_engines.dots_markdown(raw)
    assert md == "# 题目\n\n$$\na+b\n$$\n\n<table><tr><td>1</td></tr></table>\n\n正文"
    assert len(blocks) == 6
    assert ocr_engines.dots_markdown("not json") == ("not json", [])
