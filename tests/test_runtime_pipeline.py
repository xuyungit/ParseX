"""Fixed-sequence runtime (plan P1-10) end to end on synthetic documents with fake services."""

import io
import json

import fitz
import pytest
from docx import Document
from PIL import Image

from parserx.config.schema import ParserXConfig
from parserx.hierarchy.docx_styles import propose_docx_structure
from parserx.hierarchy.engine_titles import ACTOR as ENGINE_ACTOR, engine_titles
from parserx.hierarchy.levels import unify_levels
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.schema import validate_sidecar
from parserx.ir.state import DocumentState
from parserx.pipeline import Pipeline
from parserx.runtimes.pipeline import run
from tests.test_layout_routing import FakeDetector
from tests.test_tools_contract import _config, _context


class _Session:
    def __init__(self, base):
        self.base, self.context = base, None

    def __call__(self, ws, config):
        if self.context is None:
            base = self.base

            class Context(base):
                def _new_detector(self):
                    return FakeDetector()

            self.context = Context(ws, config)
        return self.context


def _png(w, h, seed):
    """A textured image (a flat colour would rightly be a blank, decorative image)."""
    import numpy as np

    buf = io.BytesIO()
    Image.fromarray(np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "Annual Report", fontsize=20, fontname="hebo")
    for i in range(8):
        page.insert_text((72, 140 + 16 * i), f"Body line {i} of the report with enough words in it.", fontsize=10)
    page.insert_image(fitz.Rect(72, 400, 372, 625), stream=_png(400, 300, 1))
    scanned = doc.new_page(width=595, height=842)
    scanned.insert_image(scanned.rect, stream=_png(300, 424, 2))
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def _strip_stats(sidecar: str) -> dict:
    data = json.loads(sidecar)
    data.pop("stats")
    return data


def test_pdf_runs_through_every_step_and_balances(pdf, tmp_path):
    outcome = run(pdf, tmp_path / "ws", tmp_path / "out", _config(), context_factory=_Session(_context()))
    assert outcome.status == "complete"
    sidecar = json.loads(outcome.sidecar_json)
    assert validate_sidecar(sidecar) == [] and sidecar["accounting"]["unassigned"] == 0
    assert [e.tool for e in outcome.envelopes] == ["workspace_init", "process", "export"]
    steps = [s.step for s in outcome.envelopes[1].result.steps]
    assert steps == ["recognize", "layout", "describe_figure", "structure", "check"]
    assert "SENTINEL-OCR 扫描文字 3 件" in outcome.markdown and "> [图片语义] photo" in outcome.markdown
    assert outcome.markdown.startswith("<!-- PAGE 1 -->\n\n# Annual Report")  # adapter:v1 title
    assert "\n## SENTINEL-OCR 标题\n" in outcome.markdown  # the scan engine's paragraph_title, under it
    actors = {b["text"]: b["decisions"][-1]["actor"] for b in sidecar["blocks"] if b["kind"] == "title" and b["level"]}
    assert actors == {"Annual Report": "adapter:v1", "SENTINEL-OCR 标题": ENGINE_ACTOR}


def test_output_is_byte_identical_across_runs(pdf, tmp_path):
    first = run(pdf, tmp_path / "ws1", tmp_path / "out1", _config(), context_factory=_Session(_context()))
    again = run(pdf, tmp_path / "ws2", tmp_path / "out2", _config(), context_factory=_Session(_context()))
    assert first.markdown == again.markdown
    assert _strip_stats(first.sidecar_json) == _strip_stats(again.sidecar_json)


def test_docx_structure_from_styles(tmp_path):
    doc = Document()
    doc.add_paragraph("Project Proposal", style="Title")
    doc.add_paragraph("Background", style="Heading 1")
    doc.add_paragraph("Body text.")
    doc.add_paragraph("Scope", style="Heading 2")
    doc.add_paragraph("first item", style="List Number")
    path = tmp_path / "doc.docx"
    doc.save(path)
    outcome = run(path, tmp_path / "ws", tmp_path / "out", _config(), context_factory=_Session(_context()))
    assert outcome.markdown == ("# Project Proposal\n\n## Background\n\nBody text.\n\n### Scope\n\n"
                                "1. first item\n")
    list_block = next(b for b in json.loads(outcome.sidecar_json)["blocks"] if b["text"] == "1. first item")
    assert list_block["kind"] == "list"


def test_docx_headings_the_styles_do_not_declare_join_the_outline(tmp_path, monkeypatch):
    # Q48: a hand-formatted document — v1's typographic detection (stubbed here) supplies the undeclared titles,
    # matched in reading order (the table-of-contents line "1.1 范围 3" is not the heading), in one outline
    from parserx.runtimes import v1_structure

    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("1.1 范围 3")
    doc.add_paragraph("第一章 总则", style="Heading 1")
    doc.add_paragraph("1.1 范围")
    doc.add_paragraph("本规范适用于桥梁支座。")
    doc.add_paragraph("1.2 术语")
    doc.add_paragraph("下列术语适用于本规范。")
    path = tmp_path / "doc.docx"
    doc.save(path)
    monkeypatch.setattr(v1_structure, "v1_headings_docx",
                        lambda p, c: [(1, "第一章 总则"), (3, "1.1 范围"), (3, "1.2 术语")])
    outcome = run(path, tmp_path / "ws", tmp_path / "out", _config(), context_factory=_Session(_context()))
    assert outcome.markdown == ("目录\n\n1.1 范围 3\n\n# 第一章 总则\n\n## 1.1 范围\n\n本规范适用于桥梁支座。\n\n"
                                "## 1.2 术语\n\n下列术语适用于本规范。\n")
    actors = {b["text"]: [d["actor"] for d in b["decisions"] if d["stage"] in ("heading_role", "heading_level")]
              for b in json.loads(outcome.sidecar_json)["blocks"]}
    assert "adapter:v1" in actors["1.1 范围"] and "adapter:v1" not in actors["第一章 总则"]


def test_v1_failing_on_a_docx_leaves_the_declared_titles(tmp_path, monkeypatch):
    from parserx.runtimes import v1_structure

    def broken(path, config):
        raise ValueError("unreadable")

    doc = Document()
    doc.add_paragraph("Background", style="Heading 1")
    doc.add_paragraph("Body text.")
    doc.save(tmp_path / "doc.docx")
    monkeypatch.setattr(v1_structure, "v1_headings_docx", broken)
    outcome = run(tmp_path / "doc.docx", tmp_path / "ws", tmp_path / "out", _config(),
                  context_factory=_Session(_context()))
    assert outcome.markdown == "# Background\n\nBody text.\n"


def test_v2_switch_returns_a_parse_result_with_sidecar(tmp_path):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Native only", fontsize=11)
    path = tmp_path / "native.pdf"
    doc.save(path)
    config = ParserXConfig(pipeline="v2")
    config.runtime.layout_shadow = False
    result = Pipeline(config).parse_result(path)
    assert "Native only" in result.markdown and result.document_status == "complete"
    assert json.loads(result.sidecar_json)["format"] == "pdf" and result.api_calls == {"ocr": 0, "vlm": 0, "llm": 0}


# ── Structure helpers ───────────────────────────────────────────────────


def test_levels_unify_numbering_and_skips():
    levels = unify_levels([("a", "第一章 总则", 1), ("b", "1.1 范围", 3), ("c", "1.2 术语", 2), ("d", "1.3 符号", 2),
                           ("e", "附录", 4)])
    assert levels == {"a": 1, "b": 2, "c": 2, "d": 2, "e": 3}
    # a pattern pulled up by the step rule stays at that level for the rest of the document
    levels = unify_levels([("a", "第一章 总则", 1), ("b", "1.1 范围", 3), ("c", "1.2 术语", 3)])
    assert levels == {"a": 1, "b": 2, "c": 2}


def test_docx_title_shifts_headings_and_body_outline_is_ignored(tmp_path):
    from parserx.content.docx import extract_docx
    from docx.oxml import parse_xml

    doc = Document()
    doc.add_paragraph("Report", style="Title")
    doc.add_paragraph("Chapter", style="Heading 1")
    body = doc.element.body
    body[-1].addprevious(parse_xml(
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:pPr>'
        '<w:outlineLvl w:val="9"/></w:pPr><w:r><w:t>Explicit body level</w:t></w:r></w:p>'))
    path = tmp_path / "d.docx"
    doc.save(path)
    state = extract_docx(path).to_state(doc_id="d", source="d.docx", source_sha256="0" * 64)
    changes = propose_docx_structure(state)
    levels = {c["block"]: c["level"] for c in changes if c["op"] == "set_level"}
    texts = {b.id: b.text for b in state.blocks}
    assert {texts[b]: lv for b, lv in levels.items()} == {"Report": 1, "Chapter": 2}


def test_scan_engine_title_labels_propose_levels():
    def title(bid, text, label, engine="paddleocr"):
        anchor = PdfAnchor(page=1, bbox=(0, 0, 1, 1), coord_space="page_pt")
        obs = Observation(id=f"o-{bid}", engine=engine, engine_version="1", task=TaskKind.RECOGNIZE, anchor=anchor,
                          label=label, text=text, status=ObservationStatus.OK)
        return Block(id=bid, kind=BlockKind.TITLE, order=int(bid[1:]), anchors=[anchor], observations=[obs],
                     chosen_observation=obs.id, text=text)

    state = DocumentState(id="d", source="d.pdf", source_sha256="0" * 64, format="pdf",
                          status=DocumentStatus.IN_PROGRESS,
                          blocks=[title("b1", "Survey Report", "doc_title"), title("b2", "1 Background", "paragraph_title"),
                                  title("b3", "1.1 Scope", "paragraph_title"), title("b4", "2 Method", "paragraph_title"),
                                  title("b5", "Native heading", None, engine="native_pdf")])
    assert [(b, lv) for b, _, lv, _ in engine_titles(state)] == [("b1", 1), ("b2", 2), ("b3", 3), ("b4", 2)]
    # a title read inside an embedded image is the image's own layout, not the document outline (Q42)
    from parserx.ir.anchor import AssetAnchor

    inside = title("b6", "Settings", "paragraph_title")
    inside = inside.model_copy(update={"anchors": [AssetAnchor(asset="a-0000000000000000", bbox=(0, 0, 9, 9),
                                                               image_size=(10, 10))]})
    state.blocks.append(inside)
    assert "b6" not in [b for b, _, _, _ in engine_titles(state)]


def test_parse_to_a_directory_writes_the_package(tmp_path, monkeypatch):
    # Q42: `parserx parse <doc> -o <dir> --pipeline v2` gives Markdown, images/, the summary and the sidecar
    import sys

    import parserx.cli

    doc = Document()
    doc.add_paragraph("Background", style="Heading 1")
    doc.add_paragraph("Body text.")
    path = tmp_path / "report.docx"
    doc.save(path)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["parserx", "parse", str(path), "-o", str(out), "--pipeline", "v2"])
    parserx.cli.main()
    assert sorted(p.name for p in out.iterdir()) == ["report.blocks.json", "report.json", "report.md"]
    assert (out / "report.md").read_text() == "# Background\n\nBody text.\n"
    summary = json.loads((out / "report.json").read_text())
    assert summary["status"] == "complete" and summary["outline"][0]["text"] == "Background"


def test_v1_docx_headings_do_not_render_drawings(tmp_path, monkeypatch):
    # Docling renders a drawing (a shape, a chart) through LibreOffice; in a sandbox that cannot run LibreOffice
    # its whole read failed.  The adapter only needs the headings: drawings are skipped.
    import docling.backend.docx.drawingml.utils as dml
    from docx.oxml import parse_xml

    from parserx.config.schema import load_config
    from parserx.runtimes.v1_structure import v1_headings_docx

    def no_libreoffice(*args, **kwargs):
        raise RuntimeError("LibreOffice unavailable")

    monkeypatch.setattr(dml.subprocess, "run", no_libreoffice)
    doc = Document()
    doc.add_paragraph("第一章 总则", style="Heading 1")
    doc.element.body[-1].addprevious(parse_xml(
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:r><w:drawing><wp:inline>'
        '<wp:extent cx="914400" cy="914400"/><wp:docPr id="1" name="Shape"/><a:graphic>'
        '<a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"/></a:graphic>'
        '</wp:inline></w:drawing></w:r></w:p>'))
    doc.add_paragraph("本规范适用于桥梁支座。")
    doc.save(tmp_path / "shape.docx")
    headings = v1_headings_docx(tmp_path / "shape.docx", load_config(None))
    assert [text for _, text in headings] == ["第一章 总则"]

