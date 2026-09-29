"""P0 contract (scripts/vision_first/p0_contract.py): the three checks, the mechanical repair, the rendering."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vision_first"))

import p0_contract as c  # noqa: E402


def _page(n=6, engine=None, tables=None):
    return {"page_id": "doc_p1", "image": {"width": 1000, "height": 1400},
            "lines": [{"id": f"L{k}", "text": t, "_blk": k // 3} for k, t in enumerate(
                ["页眉", "第一行正文", "接着第二行", "x2 + y = 1", "(1)", "图里的字"][:n], 1)],
            "engine": engine or [], "tables": tables or []}


def _part(kind, lines, text="", scripts=False):
    return {"kind": kind, "lines": lines, "text": text, "engine": [], "scripts": scripts}


def _answer(**over):
    data = {"blocks": [
        {"id": "B1", "type": "text", "level": None, "region": None, "parts": [_part("copy", ["L2-L3"])]},
        {"id": "B2", "type": "formula", "level": None, "region": None,
         "parts": [_part("write", ["L4", "L5"], "x^{2} + y = 1 \\tag{1}")]},
        {"id": "B3", "type": "figure", "level": None, "region": [10, 10, 500, 400], "parts": []}],
        "aside": [{"lines": ["L1"], "reason": "页眉", "to": "excluded"},
                  {"lines": ["L6"], "reason": "图内文字", "to": "B3"}],
        "unresolved": []}
    data.update(over)
    return data


def test_valid_answer_passes_all_three_checks():
    checked = c.check(json.dumps(_answer(), ensure_ascii=False), _page())
    assert checked.level == "valid", checked.problems


def test_the_three_levels_and_the_empty_answer():
    assert c.check("", _page()).level == "empty"
    assert c.check("{\"blocks\": [", _page()).level == "unparseable"
    assert c.check(json.dumps({"blocks": []}), _page()).level == "schema"
    fenced = "```json\n" + json.dumps(_answer(), ensure_ascii=False) + "\n```"
    assert c.check(fenced, _page()).level == "valid"


def test_every_line_exactly_once_and_references_exist():
    answer = _answer(aside=[{"lines": ["L1", "L2"], "reason": "页眉", "to": "excluded"},
                            {"lines": ["L9"], "reason": "x", "to": "B7"}])
    problems = c.check(json.dumps(answer, ensure_ascii=False), _page()).problems
    assert any("L2 出现了不止一次" in p for p in problems)
    assert any("L6" in p and "没有分配" in p for p in problems)
    assert any("L9 不存在" in p for p in problems)
    assert any("B7" in p for p in problems)


def test_written_text_keeps_latex_balanced_and_no_unmapped_code_point():
    answer = _answer()
    answer["blocks"][1]["parts"][0]["text"] = "\\frac{x}{2 \ue5d2"
    problems = c.check(json.dumps(answer, ensure_ascii=False), _page()).problems
    assert any("花括号" in p for p in problems) and any("U+E5D2" in p for p in problems)


def test_repair_drops_repeats_and_copies_unallocated_lines_back_in_place():
    answer = _answer(aside=[{"lines": ["L1", "L3"], "reason": "页眉", "to": "excluded"}])  # L3 twice, L6 nowhere
    repaired, done = c.repair(answer, _page())
    assert c.invariant_problems(repaired, _page()) == []
    assert repaired["aside"][0]["lines"] == ["L1"]
    added = [b for b in repaired["blocks"] if b.get("repaired")]
    assert [p["lines"] for b in added for p in b["parts"]] == [["L6"]]
    assert repaired["blocks"].index(added[0]) == 2  # after B2, which holds L5
    assert len(done) == 2


def test_render_copies_writes_marks_and_uses_the_extraction_grid():
    page = _page(tables=[{"block": "t1", "lines": ["L2", "L3"], "html": "<table><tr><td>a</td></tr></table>"}])
    answer = _answer()
    answer["blocks"].insert(0, {"id": "B0", "type": "title", "level": 1, "region": None,
                                "parts": [_part("write", [], "标题 \ue5d2")]})
    answer["blocks"][0]["region"] = [0, 0, 10, 10]
    answer["blocks"].append({"id": "B9", "type": "table", "level": None, "region": None,
                             "parts": [_part("table", ["L2-L3"])]})
    md = c.render(answer, page)
    assert "# 标题 〔?〕" in md
    assert "第一行正文 接着第二行" not in md and "第一行正文接着第二行" in md
    assert "$$\nx^{2} + y = 1 \\tag{1}\n$$" in md
    assert "<table><tr><td>a</td></tr></table>" in md
    assert "图里的字" not in md


def test_fallback_copies_every_line():
    data = c.fallback(_page())
    assert c.invariant_problems(data, _page()) == []


def test_a_grid_holding_two_tables_the_model_split_is_rendered_once():
    page = _page(tables=[{"block": "t1", "lines": ["L2", "L3", "L4"], "html": "<table><tr><td>g</td></tr></table>"}])
    answer = _answer(blocks=[
        {"id": "B1", "type": "table", "level": None, "region": None, "parts": [_part("table", ["L2"])]},
        {"id": "B2", "type": "table", "level": None, "region": None, "parts": [_part("table", ["L3-L4"])]}])
    md = c.render(answer, page)
    assert md.count("<table>") == 1 and "接着第二行" not in md


def test_scripts_write_the_candidates_in_copied_lines_and_single_line_cells():
    page = _page(tables=[{"block": "t1", "lines": ["L4", "L5"], "html": "<table><tr><td>x2 +  y = 1</td><td>(1)</td></tr></table>"}])
    page["lines"][3]["_scripted"] = "x<sup>2</sup> + y = 1"
    answer = _answer(blocks=[
        {"id": "B1", "type": "text", "level": None, "region": None, "parts": [_part("copy", ["L4"], scripts=True)]},
        {"id": "B2", "type": "text", "level": None, "region": None, "parts": [_part("copy", ["L4"])]},
        {"id": "B3", "type": "table", "level": None, "region": None, "parts": [_part("table", ["L4-L5"], scripts=True)]}])
    md = c.render(answer, page)
    assert md.count("x<sup>2</sup> + y = 1") == 2  # the copy with scripts and the cell
    assert "\n\nx2 + y = 1\n\n" in md  # the copy without
