"""Display formulas asked apart from the allocation (scripts/vision_first/v_formulas.py, contract v5)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vision_first"))

import v_adapter  # noqa: E402
import v_formulas as f  # noqa: E402


def _data():
    return {"blocks": [
        {"id": "B1", "type": "text", "level": None, "region": None,
         "parts": [{"kind": "copy", "lines": ["L1"], "text": "", "engine": []}]},
        {"id": "B2", "type": "formula", "level": None, "region": None, "parts": [
            {"kind": "write", "lines": ["L2-L3"], "text": "", "engine": ["E1"]},
            {"kind": "copy", "lines": ["L4"], "text": "", "engine": []}]},
        {"id": "B3", "type": "formula", "level": None, "region": None,
         "parts": [{"kind": "write", "lines": ["L5"], "text": "y = 2 \\tag{2}", "engine": []}]}],
        "aside": [], "unresolved": []}


def _page():
    boxes = [[10, 10, 90, 20], [20, 40, 60, 50], [62, 38, 80, 52], [150, 40, 170, 50], [20, 80, 60, 90]]
    return {"page_id": "doc_p1", "page": 1, "image": {"width": 200, "height": 100, "dpi": 150},
            "lines": [{"id": f"L{k}", "text": t, "box": b} for k, (t, b) in enumerate(
                zip(["正文", "x", "2", "(1)", "y = 2 (2)"], boxes), 1)],
            "engine": [{"id": "E1", "box": [18, 36, 100, 54], "label": "display_formula", "text": "x^{2}"}]}


def test_the_formulas_their_lines_engine_entries_and_boxes():
    wanted = f.wanted(_data())
    assert [(w["id"], w["lines"], w["engine"]) for w in wanted] == [
        ("B2", ["L2", "L3", "L4"], ["E1"]), ("B3", ["L5"], [])]
    assert f.crop_box(_page(), wanted[0]) == [12, 30, 176, 60]  # lines and entry, with the margin
    assert json.loads(f.context(_page(), wanted).split("\n")[1]) == {
        "id": "B2", "text_layer": ["x", "2", "(1)"], "engine": ["x^{2}"]}


def test_every_formula_once_balanced_and_what_is_usable_of_a_partial_answer():
    answer = {"formulas": [{"id": "B2", "latex": "x^{2 \\tag{1}"}, {"id": "B9", "latex": "z"}]}
    checked = f.check(json.dumps(answer), ["B2", "B3"])
    assert checked.level == "invariants"
    assert any("B3" in p and "恰好一项" in p for p in checked.problems)
    assert any("B9" in p for p in checked.problems) and any("花括号" in p for p in checked.problems)
    good = {"formulas": [{"id": "B2", "latex": "x^{2} \\tag{1}"}]}
    assert f.usable(f.check(json.dumps(good), ["B2", "B3"]), ["B2", "B3"]) == {"B2": "x^{2} \\tag{1}"}


def test_fill_makes_each_formula_one_write_and_keeps_the_allocations_text_when_none_came():
    filled = f.fill(_data(), {"B2": "x^{2} \\tag{1}"})
    assert filled["blocks"][1]["parts"] == [
        {"kind": "write", "lines": ["L2-L3", "L4"], "text": "x^{2} \\tag{1}", "engine": ["E1"]}]
    assert filled["blocks"][2]["parts"][0]["text"] == "y = 2 \\tag{2}"
    assert filled["blocks"][0] == _data()["blocks"][0]


def test_a_rewrite_that_leaves_out_its_lines_script_candidates_is_told():
    lines = [{"scripts": [{"base": "k", "t": "１", "kind": "sub"}]}]
    assert not v_adapter._scripts_left_out("刚度 $k_{1}=6$ MN/m", lines)
    assert not v_adapter._scripts_left_out("刚度 k₁=6", lines)
    assert v_adapter._scripts_left_out("刚度 k1=6", lines)


def test_a_script_split_over_two_lines_is_one_in_the_writing():
    lines = [{"scripts": [{"base": "q", "t": "x′+2", "kind": "sup"}]}, {"scripts": [{"base": "q", "t": "m", "kind": "sup"}]}]
    assert not v_adapter._scripts_left_out("$\\Delta q_2^{x'+2m}$", lines)
    assert not v_adapter._scripts_left_out("$\\Delta q_2^{x^{\\prime}+2m}$", lines)
    assert v_adapter._scripts_left_out("$\\Delta q_2 x'+2m$", lines)
