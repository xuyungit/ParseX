"""Formulas of native pages (Q70, version 2): pages with a detected formula are read whole; a passage takes the
page reading when it conserves the text layer's letters and digits, else an editor's version that does, else the
text layer stays and the passage is a review item."""

import json

import pymupdf

from parserx.layout.detector import Region
from parserx.tools import call_tool, workspace_init
from parserx.reading.compare import normalize
from parserx.content.latex import characters as _symbols
from parserx.content.latex import placed
from parserx.tools.views import unresolved_items
from parserx.workspace import Workspace
from tests.test_tools_contract import _config, _context


def test_greek_commands_read_as_their_letters():
    assert _symbols(r"\delta_{a}+\Delta q") == "δ_{a}+Δ q"


def test_command_names_are_not_characters_of_the_text():
    # "\left" carries no l: a reading that turned the superscript l into 1 loses it
    assert normalize(_symbols(r"\left(\begin{array}{cc}\Delta^{1}&\mathbf{0}\end{array}\right)")) == "δ10"
    assert normalize(_symbols(r"\Delta^{\ell}")) == "δl"  # ℓ is the letter l


def test_a_line_break_is_no_character_and_the_letter_after_it_is_one():
    assert normalize(_symbols(r"\begin{aligned}f_{ij}&=1\\f_{ij}&=0\end{aligned}")) == "fij1fij0"


def test_characters_are_read_with_the_script_they_are_written_in():
    def seen(text):
        return [(c, k) for c, k in placed(text) if c.isalnum()]

    assert seen(r"式中 $N_o$ 与 No") == [("式", ""), ("中", ""), ("N", ""), ("o", "sub"), ("与", ""), ("N", ""), ("o", "")]
    # LaTeX, a Unicode script form and HTML write the same thing; in a script inside a script, the inner one
    assert seen(r"$m^{2}$") == seen("m²") == seen("m<sup>2</sup>") == [("m", ""), ("2", "sup")]
    assert seen(r"$e^{x_1}$ \(J_K^T\)") == [("e", ""), ("x", "sup"), ("1", "sub"), ("J", ""), ("K", "sub"), ("T", "sup")]
    # outside mathematics an underscore is no script; commands are their letters, markup none
    assert seen(r"file_1 $\mathrm{CDCl_{3}}$ $\mu$L") == [
        ("f", ""), ("i", ""), ("l", ""), ("e", ""), ("1", ""), ("C", ""), ("D", ""), ("C", ""), ("l", ""),
        ("3", "sub"), ("μ", ""), ("L", "")]


def test_operator_names_print_their_letters():
    assert normalize(_symbols(r"\min\|Ax-b\|+\sin x")) == "minaxbsinx"


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


def _columns_pdf(tmp_path):
    """Two columns; the paragraph at the foot of the first goes on at the head of the second."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "Opening prose of the first column, plainly set.", fontsize=10)
    page.insert_text((72, 780), "Here E = mc", fontsize=10)
    page.insert_text((128, 775), "2", fontsize=6)
    page.insert_text((140, 780), "gives the energy which", fontsize=10)
    page.insert_text((320, 90), "the second column goes on to explain fully.", fontsize=10)
    path = tmp_path / "f.pdf"
    doc.save(path)
    return path


def _run(tmp_path, entries, editor_answer=None, pdf=_page_pdf, formula=(70, 118, 140, 134)):
    path = pdf(tmp_path)
    k = 100 / 72  # the layout step renders pages at 100 dpi

    class Detector:
        name, version = "layout", "fake-formula-detector"

        def detect(self, png):
            return [Region(bbox=tuple(v * k for v in formula), label="inline_formula", score=0.9)]

    def ocr_page():
        return {"prunedResult": {"width": 595, "height": 842, "parsing_res_list": entries}}

    base = _context(page=ocr_page)

    class Editor:
        calls = 0
        contexts: list[str] = []

        def describe_image(self, image_path, prompt, **kw):
            Editor.calls += 1
            Editor.contexts.append(kw.get("context") or "")
            if isinstance(editor_answer, list):  # one answer per round
                return editor_answer[min(Editor.calls, len(editor_answer)) - 1]
            return editor_answer or ""

    class Context(base):
        def _new_detector(self):
            return Detector()

        def _new_vlm(self, cfg):
            return Editor() if editor_answer is not None else super()._new_vlm(cfg)

    config = _config()
    config.runtime.describe_figures = False
    workspace_init(path, tmp_path / "ws", config=config)
    envelope, _ = call_tool("run_pipeline", tmp_path / "ws", {}, config=config, context_factory=Context)
    assert envelope.ok, envelope.failures
    call_tool("export", tmp_path / "ws", {"out": str(tmp_path / "out"), "name": "f"}, config=config,
              context_factory=Context)
    _run.contexts = Editor.contexts
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
    assert "0×2、6×1、8×1、5×1" in _run.contexts[-1]  # the editor is told what the reading lacks


def test_an_editor_version_that_still_loses_characters_gets_a_second_round(tmp_path):
    state, md, calls = _run(tmp_path, [
        _entry("text", "Here $ E=mc^{2} $", (72, 120, 140, 133)),
        _entry("display_formula", "$$ F=ma $$", (110, 190, 170, 203)),
        PROSE,
    ], editor_answer=["[0068] $F=ma$", "[0068] $F=ma$ (5)"])
    assert calls == 2 and "[0068] $F=ma$ (5)" in md
    assert "B：\n[0068] $F=ma$" in _run.contexts[-1] and "5×1" in _run.contexts[-1]


def test_without_a_conserving_version_the_text_layer_stays_and_the_agent_is_asked(tmp_path):
    state, md, calls = _run(tmp_path, [
        _entry("display_formula", "$$ F=ma $$", (110, 190, 170, 203)),
        PROSE,
    ], editor_answer="$F=ma$")
    assert calls == 2 and "[0068] F = ma" in md
    items = [u for u in unresolved_items(state) if u.kind.value == "formula_candidate"]
    assert len(items) == 1 and items[0].quotes[0].doc_text == "$F=ma$"


def test_a_paragraph_the_engine_returns_line_by_line_stays_one_paragraph(tmp_path):
    state, md, _ = _run(tmp_path, [
        _entry("text", "Here $ E=mc^{2} $", (72, 120, 100, 133)),
        _entry("text", "holds.", (100, 120, 140, 133)),  # the same native line, cut in two by the engine
        PROSE,
    ])
    assert "Here $ E=mc^{2} $ holds." in md


def test_a_paragraph_the_engine_joins_across_the_column_break_is_not_repeated(tmp_path):
    # the engine reads the paragraph whole at the foot of the first column; the head of the second is its end
    state, md, _ = _run(tmp_path, [
        _entry("text", "Opening prose of the first column, plainly set.", (72, 80, 300, 92)),
        _entry("text", "Here $ E=mc^{2} $ gives the energy which the second column goes on to explain fully.",
               (72, 770, 260, 782)),
    ], pdf=_columns_pdf, formula=(70, 768, 140, 784))
    assert "Here $ E=mc^{2} $ gives the energy which the second column goes on to explain fully." in md
    assert md.count("the second column goes on to explain fully") == 1


def test_a_piece_the_text_layer_cut_from_a_formula_goes_with_its_passage():
    from parserx.ir.anchor import PdfAnchor
    from parserx.ir.block import Block
    from parserx.ir.enums import BlockKind
    from parserx.tools.formulas import _enclosed

    def block(bid, box):
        return Block(id=bid, kind=BlockKind.TEXT, order=0, anchors=[PdfAnchor(page=1, bbox=box, coord_space="page_pt")])

    formula, prime, number = block("f", (307, 350, 520, 719)), block("p", (348, 351, 356, 357)), \
        block("n", (500, 417, 543, 428))
    passages = _enclosed([formula, prime, number], [(["f"], [block("r", (304, 358, 520, 719))])])
    assert passages[0][0] == ["f", "p"]  # the prime above the line; the number at the right is not inside


def _kept_passage(tmp_path):
    """A passage kept from the text layer, its page reading a formula_candidate item: (workspace, the item's block)."""
    state, _, _ = _run(tmp_path, [_entry("display_formula", "$$ F=ma $$", (110, 190, 170, 203)), PROSE],
                       editor_answer="$F=ma$")
    return tmp_path / "ws", next(u for u in unresolved_items(state) if u.kind.value == "formula_candidate").target


def _transcribe(ws, block, text, *, looked_at=None):
    from tests.test_tools_contract import _call, _evidence

    context = _context()
    evidence = _evidence(ws, context, block=looked_at or block, **{"as": "image"})
    env, _ = _call("edit_draft", ws, {"ops": [{"op": "transcribe_passage", "block": block, "text": text,
                                               "reason": "照原件写这一段", "evidence": evidence}]}, context=context)
    assert env.ok, env.failures
    return env.result.outcomes[0], evidence


def test_the_agent_writes_a_kept_passage_whole_and_can_take_it_back(tmp_path):
    from tests.test_tools_contract import _call

    ws, owner = _kept_passage(tmp_path)
    out, evidence = _transcribe(ws, owner, "[0068] $$F = ma$$ (5)")
    assert out.accepted and out.block
    state = Workspace.open(ws).load()
    made = next(b for b in state.blocks if b.id == out.block)
    assert made.text == "[0068] $$F = ma$$ (5)" and made.observations[0].engine == "agent"
    assert next(b for b in state.blocks if b.id == owner).status.value == "duplicate"  # kept, not output
    assert not [u for u in unresolved_items(state) if u.kind.value == "formula_candidate"]
    env, _ = _call("edit_draft", ws, {"ops": [{"op": "unadopt", "evidence": evidence, "reason": "写错了"}]},
                   context=_context())
    assert env.result.outcomes[0].accepted
    state = Workspace.open(ws).load()
    assert next(b for b in state.blocks if b.id == owner).status.value == "ok"
    assert [u for u in unresolved_items(state) if u.kind.value == "formula_candidate"]  # the item is back


def test_a_transcription_keeps_every_letter_and_digit_adds_none_unseen_and_renders(tmp_path):
    ws, owner = _kept_passage(tmp_path)
    out, _ = _transcribe(ws, owner, "$$F = ma$$ (5)")  # the paragraph number left out
    assert not out.accepted and out.rule == "conservation" and "0×2" in out.detail
    out, _ = _transcribe(ws, owner, "[0068] $$F = m a_x$$ (5)")  # an x no reading of the place has
    assert not out.accepted and out.rule == "not_seen" and "x×1" in out.detail
    out, _ = _transcribe(ws, owner, "[0068] $$F = ma (5)")
    assert not out.accepted and out.rule == "latex" and "$$" in out.detail


def test_a_transcription_rests_on_a_look_at_the_whole_passage_of_a_pending_item(tmp_path):
    ws, owner = _kept_passage(tmp_path)
    prose = next(b.id for b in Workspace.open(ws).load().blocks if (b.text or "").startswith("A plain sentence"))
    out, _ = _transcribe(ws, owner, "[0068] $$F = ma$$ (5)", looked_at=prose)  # a look at another place
    assert not out.accepted and out.rule == "image_evidence"
    out, _ = _transcribe(ws, prose, "A plain sentence of prose that the page reading repeats word for word.")
    assert not out.accepted and out.rule == "not_candidate"


def test_the_formula_editor_asks_its_own_model_else_the_service_model():
    # tools.formula_editor names luna (the 2026-10-02 replay: the service model qwen3.8-flash copied a reading's lost
    # superscript); an entry that cannot be used, or none, leaves the service model
    from types import SimpleNamespace

    from parserx.config.schema import Reader
    from parserx.tools.envelope import FailureCode, ToolFailure
    from parserx.tools.formulas import editor_service

    def ctx(editor, using):
        tools = SimpleNamespace(formula_editor=editor, ask_reasoning_effort="low")
        return SimpleNamespace(config=SimpleNamespace(tools=tools), vlm_using=using,
                               vlm=lambda effort: ("service", effort))

    def unconfigured(name, effort):
        raise ToolFailure(FailureCode.SERVICE_ERROR, f"{name} has no key")

    luna = Reader(use="gpt-6-luna", reasoning_effort="low")
    assert editor_service(ctx(luna, lambda name, effort: (name, effort))) == ("gpt-6-luna", "low")
    assert editor_service(ctx(luna, lambda name, effort: None)) == ("service", "low")  # no such entry
    assert editor_service(ctx(luna, unconfigured)) == ("service", "low")
    assert editor_service(ctx(None, lambda name, effort: (name, effort))) == ("service", "low")
