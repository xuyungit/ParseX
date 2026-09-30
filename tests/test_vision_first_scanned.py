"""Scanned-page contract (scripts/vision_first/s0_contract.py): the scan engine's blocks K1… as the units of complete
allocation, with the native page's checks, repair and rendering (p0_contract, unit ``K``)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vision_first"))

import p0_contract as c  # noqa: E402

TABLE = "<table><tr><td>种类</td><td>强度</td></tr><tr><td>HPB300</td><td>250</td></tr></table>"


def _page():
    units = [("header", "规范（JTG 3362—2018）", "规范（JTG 3362—2018）"), ("text", "第一段，共 12 个测点。", "第一段，共 12 个测点。"),
             ("display_formula", "$$x^{2}+y=1$$", "x^{2}+y=1"), ("table", TABLE, None), ("number", "3", "3")]
    return {"page_id": "doc_p1", "unit": "K", "image": {"width": 1000, "height": 1400}, "engine": None,
            "lines": [{"id": f"K{k}", "label": label, "box": [0, 0, 10, 10], "text": text, "shown": shown, "_blk": k}
                      for k, (label, text, shown) in enumerate(units, 1)],
            "tables": [{"block": "K4", "lines": ["K4"], "html": TABLE}], "local": [], "regions": []}


def _part(kind, lines, text="", cell=None):
    return {"kind": kind, "lines": lines, "text": text, "engine": [], "cell": cell}


def _answer(**over):
    data = {"blocks": [
        {"id": "B1", "type": "text", "level": None, "region": None, "parts": [_part("copy", ["K2"])]},
        {"id": "B2", "type": "formula", "level": None, "region": None, "parts": [_part("copy", ["K3"])]},
        {"id": "B3", "type": "table", "level": None, "region": None,
         "parts": [_part("table", ["K4"]), _part("cell", [], "251", "T1:2:2")]}],
        "aside": [{"lines": ["K1", "K5"], "reason": "页眉、页码", "to": "excluded"}], "unresolved": []}
    data.update(over)
    return data


def test_engine_blocks_are_the_units():
    page = _page()
    assert c.check(json.dumps(_answer(), ensure_ascii=False), page).level == "valid"
    lost = _answer(aside=[{"lines": ["K1"], "reason": "页眉", "to": "excluded"}])
    assert any("没有分配的引擎块：K5" in p for p in c.check(json.dumps(lost, ensure_ascii=False), page).problems)
    lines = _answer(blocks=[{"id": "B1", "type": "text", "level": None, "region": None, "parts": [_part("copy", ["L2"])]}])
    assert any("'L2' 不是引擎块号" in p for p in c.check(json.dumps(lines, ensure_ascii=False), page).problems)


def test_a_written_formula_on_a_scanned_page_needs_its_latex():
    written = _answer()
    written["blocks"][1]["parts"] = [_part("write", ["K3"], "")]
    assert any("text 是空的" in p for p in c.check(json.dumps(written, ensure_ascii=False), _page()).problems)


def test_render_places_the_engine_readings():
    md = c.render(_answer(), _page())
    assert "第一段，共 12 个测点。" in md and "$$\nx^{2}+y=1\n$$" in md
    assert "<td>251</td>" in md and "250" not in md and "JTG" not in md


def test_repair_copies_a_lost_engine_block_back():
    data, done = c.repair(_answer(aside=[{"lines": ["K1"], "reason": "页眉", "to": "excluded"}]), _page())
    assert any("K5" in d for d in done)
    assert c.destinations(data, _page())["K5"].startswith("copy:")


def test_text_copied_into_a_figure_is_its_in_image_text():
    data = _answer()
    data["blocks"].append({"id": "B4", "type": "figure", "level": None, "region": [0, 0, 100, 100],
                           "parts": [_part("copy", ["K2"])]})
    data["blocks"][0]["parts"] = [_part("write", [], "另一段。")]
    data["blocks"][0]["region"] = [0, 0, 50, 50]
    md = c.render(data, _page())
    assert "![图](doc_p1-B4.png)" in md and "> 第一段，共 12 个测点。" in md
