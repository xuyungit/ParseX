"""Display formulas of native pages as LaTeX (Q70): adopted only when the two readings agree and nothing of the
text layer is lost."""

import json

import fitz

from parserx.layout.detector import Region
from parserx.tools import call_tool, workspace_init
from parserx.tools.formulas import _agree, _symbols
from tests.test_tools_contract import _config, _context

FORMULA = [{"block_label": "display_formula"}]


def test_readings_agree_and_conserve_the_text_layer():
    assert _symbols(r"\delta_{a}+\Delta q") == "δ_{a}+Δ q"
    assert _agree(r"E=mc^{2}", "E = mc\n2", FORMULA)
    assert not _agree(r"[G]=[\delta_{a}]", "[0068] [G]=[δa] (5)", FORMULA)  # a paragraph number would be lost
    assert not _agree(r"E=mc^{2}", "E = mc\n2", [{"block_label": "text"}])  # the engine does not see a formula
    assert not _agree(r"x+y", "E = mc\n2", FORMULA)


def test_process_replaces_formula_fragments_with_latex(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "The energy of a body at rest follows from its mass as below.", fontsize=10)
    page.insert_text((250, 150), "E = mc", fontsize=11)
    page.insert_text((290, 145), "2", fontsize=7)
    page.insert_text((72, 200), "where m is the mass and c the speed of light in vacuum.", fontsize=10)
    page.insert_text((72, 260), "[0068] F = ma     (5)", fontsize=11)
    page.insert_text((72, 300), "The force is the product of mass and acceleration of the body.", fontsize=10)
    path = tmp_path / "f.pdf"
    doc.save(path)
    k = 100 / 72  # the layout step renders pages at 100 dpi

    class Detector:
        name, version = "layout", "fake-formula-detector"

        def detect(self, png):
            return [Region(bbox=(240 * k, 130 * k, 310 * k, 158 * k), label="display_formula", score=0.9),
                    Region(bbox=(110 * k, 248 * k, 170 * k, 266 * k), label="display_formula", score=0.9)]

    answers = iter(["E=mc^{2}", "F=ma"])

    def ocr_page():
        return {"prunedResult": {"width": 100, "height": 40, "parsing_res_list": [
            {"block_label": "display_formula", "block_content": f"$${next(answers)}$$", "block_bbox": [0, 0, 100, 40]}]}}

    base = _context(page=ocr_page)

    class Context(base):
        def _new_detector(self):
            return Detector()

    config = _config()
    workspace_init(path, tmp_path / "ws", config=config)
    envelope, _ = call_tool("process", tmp_path / "ws", {}, config=config, context_factory=Context)
    assert envelope.ok, envelope.failures
    state = json.loads((tmp_path / "ws" / "state.json").read_text())
    formulas = [b for b in state["blocks"] if b["kind"] == "formula" and b["status"] == "ok"]
    assert [b["text"] for b in formulas] == ["E=mc^{2}"]
    replaced = [b for b in state["blocks"] if b["status"] == "duplicate"]
    assert replaced and all(r["kind"] == "text" for r in replaced)
    assert any((b["text"] or "").startswith("[0068]") and b["status"] == "ok" for b in state["blocks"])
    envelope, _ = call_tool("export", tmp_path / "ws", {"out": str(tmp_path / "out"), "name": "f"}, config=config,
                            context_factory=Context)
    md = (tmp_path / "out" / "f.md").read_text()
    assert "$$\nE=mc^{2}\n$$" in md and "[0068] F = ma" in md
    assert json.loads((tmp_path / "out" / "f.blocks.json").read_text())["accounting"]["unassigned"] == 0
