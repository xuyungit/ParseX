"""Formulas of native pages (Q70, version 2): pages with a detected formula are read whole; a passage takes the
page reading when it conserves the text layer's letters and digits, else an editor's version that does, else the
text layer stays and the passage is a review item."""

import json

import pymupdf

from parserx.layout.detector import Region
from parserx.tools import call_tool, workspace_init
from parserx.tools.formulas import _symbols
from parserx.tools.views import unresolved_items
from parserx.workspace import Workspace
from tests.test_tools_contract import _config, _context


def test_greek_commands_read_as_their_letters():
    assert _symbols(r"\delta_{a}+\Delta q") == "δ_{a}+Δ q"


def _page_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "The rest energy follows from the mass as stated below in this short note.", fontsize=10)
    page.insert_text((72, 130), "Here E = mc", fontsize=10)
    page.insert_text((128, 125), "2", fontsize=6)
    page.insert_text((72, 200), "[0068] F = ma     (5)", fontsize=11)
    page.insert_text((72, 260), "A plain sentence of prose that the page reading repeats word for word.", fontsize=10)
    path = tmp_path / "f.pdf"
    doc.save(path)
    return path


def _run(tmp_path, entries, editor_answer=None):
    path = _page_pdf(tmp_path)
    k = 100 / 72  # the layout step renders pages at 100 dpi

    class Detector:
        name, version = "layout", "fake-formula-detector"

        def detect(self, png):
            return [Region(bbox=(70 * k, 118 * k, 140 * k, 134 * k), label="inline_formula", score=0.9)]

    def ocr_page():
        return {"prunedResult": {"width": 595, "height": 842, "parsing_res_list": entries}}

    base = _context(page=ocr_page)

    class Editor:
        calls = 0

        def describe_image(self, image_path, prompt, **kw):
            Editor.calls += 1
            return editor_answer or ""

    class Context(base):
        def _new_detector(self):
            return Detector()

        def _new_vlm(self, cfg):
            return Editor() if editor_answer is not None else super()._new_vlm(cfg)

    config = _config()
    config.runtime.describe_figures = False
    workspace_init(path, tmp_path / "ws", config=config)
    envelope, _ = call_tool("process", tmp_path / "ws", {}, config=config, context_factory=Context)
    assert envelope.ok, envelope.failures
    call_tool("export", tmp_path / "ws", {"out": str(tmp_path / "out"), "name": "f"}, config=config,
              context_factory=Context)
    return Workspace.open(tmp_path / "ws").load(), (tmp_path / "out" / "f.md").read_text(), Editor.calls


def _entry(label, text, box):
    return {"block_label": label, "block_content": text, "block_bbox": list(box)}


PROSE = _entry("text", "A plain sentence of prose that the page reading repeats word for word.", (72, 250, 450, 262))


def test_a_passage_takes_the_page_reading_when_it_conserves_the_text_layer(tmp_path):
    state, md, calls = _run(tmp_path, [
        _entry("text", "Here $ E=mc^{2} $", (72, 120, 140, 133)),
        _entry("text", "[0068] $ F=ma $ (5)", (72, 190, 190, 203)),
        PROSE,
    ])
    assert "Here $ E=mc^{2} $" in md and "[0068] $ F=ma $ (5)" in md
    assert calls == 0
    prose = [b for b in state.blocks if (b.text or "").startswith("A plain sentence")]
    assert len(prose) == 1 and prose[0].observations[0].engine == "native_pdf"  # prose stays the text layer's
    assert json.loads(state.model_dump_json())["blocks"]
    assert any(b.status.value == "duplicate" for b in state.blocks)


def test_an_editor_merges_when_the_reading_loses_characters(tmp_path):
    # the reading drops the paragraph number; the editor keeps it
    state, md, calls = _run(tmp_path, [
        _entry("text", "Here $ E=mc^{2} $", (72, 120, 140, 133)),
        _entry("display_formula", "$$ F=ma $$", (110, 190, 170, 203)),
        PROSE,
    ], editor_answer="[0068] $F=ma$ (5)")
    assert calls == 1 and "[0068] $F=ma$ (5)" in md


def test_without_a_conserving_version_the_text_layer_stays_and_the_agent_is_asked(tmp_path):
    state, md, calls = _run(tmp_path, [
        _entry("display_formula", "$$ F=ma $$", (110, 190, 170, 203)),
        PROSE,
    ], editor_answer="$F=ma$")
    assert calls == 1 and "[0068] F = ma" in md
    items = [u for u in unresolved_items(state) if u.kind.value == "formula_candidate"]
    assert len(items) == 1 and items[0].quotes[0].doc_text == "$F=ma$"


def test_a_paragraph_the_engine_returns_line_by_line_stays_one_paragraph(tmp_path):
    state, md, _ = _run(tmp_path, [
        _entry("text", "Here $ E=mc^{2} $", (72, 120, 100, 133)),
        _entry("text", "holds.", (100, 120, 140, 133)),  # the same native line, cut in two by the engine
        PROSE,
    ])
    assert "Here $ E=mc^{2} $ holds." in md
