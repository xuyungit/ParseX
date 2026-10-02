"""set_table: the agent writes a table's structure anew — rows, columns, merged cells — on its look at the page, the
characters staying the table's (F, 2026-10-01: a row a page break cut in two came out as two rows and two empty
ones; the agent asked a re-reading to merge them, could not adopt it, and moved text with set_cells instead)."""

from parserx.content.select import structure_only
from parserx.tables import TableGrid
from tests.test_tools_agent import _block, _markdown, _ok, context, draft  # noqa: F401  (fixtures)
from tests.test_tools_contract import pdf, ws  # noqa: F401  (fixtures)

CUT = ("<table>"
       "<tr><td>2</td><td>平台</td><td>软件</td><td>监控</td><td>定制化平台</td><td>1</td></tr>"
       "<tr><td rowspan='2'></td><td rowspan='2'>桥梁支座项目</td><td rowspan='2'>数据库，新产品，</td>"
       "<td rowspan='2'>应用示</td><td>示范项目（成都）</td><td>0</td></tr>"
       "<tr><td></td><td></td></tr>"
       "<tr><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
       "<tr><td>3</td><td>示范及应用推广</td><td>工程工艺</td><td>范。</td><td>示范项目</td><td>0</td></tr>"
       "</table>")
WHOLE = ("<table>"
         "<tr><td>2</td><td>平台</td><td>软件</td><td>监控</td><td>定制化平台</td><td>1</td></tr>"
         "<tr><td rowspan='2'>3</td><td rowspan='2'>桥梁支座项目示范及应用推广</td>"
         "<td rowspan='2'>数据库，新产品，工程工艺</td><td rowspan='2'>应用示范。</td><td>示范项目（成都）</td><td>0</td></tr>"
         "<tr><td>示范项目</td><td>0</td></tr>"
         "</table>")


def test_a_row_a_page_break_cut_in_two_is_written_whole():
    check = structure_only(TableGrid.from_html(CUT), TableGrid.from_html(WHOLE))
    assert check.passed, check.detail


def test_a_structure_change_writes_the_same_characters():
    current = TableGrid.from_html(CUT)
    added = structure_only(current, TableGrid.from_html(WHOLE.replace("应用示范。", "应用示范工程。")))
    assert not added.passed and "工程" in added.detail and "set_cells" in added.detail
    dropped = structure_only(current, TableGrid.from_html(WHOLE.replace("<td>示范项目</td><td>0</td>",
                                                                        "<td></td><td></td>")))
    assert not dropped.passed and "'示范项目' (r4c4)" in dropped.detail and "set_cells" in dropped.detail
    scrambled = structure_only(current, TableGrid.from_html(WHOLE.replace("应用示范。", "示应用范。")))
    assert not scrambled.passed and "应用示" in scrambled.detail  # a cell's text must read on whole


def test_the_agent_sets_a_table_on_its_look(draft, context):
    table = _block(draft, "甲")
    evidence = _ok("view_source", draft, {"looks": [{"block": table, "as": "image"}]}, context)["results"][0]["evidence"]
    one_row = {"op": "set_table", "block": table, "n_rows": 1, "n_cols": 4, "header_rows": 0,
               "cells": [{"row": 0, "col": c, "content": t} for c, t in enumerate(["项目", "数值", "SENTINEL-OCR 甲", "3"])],
               "reason": "图上是一行", "evidence": evidence}
    eight = {**one_row, "cells": [*one_row["cells"][:3], {"row": 0, "col": 3, "content": "8"}]}
    overlapping = {**one_row, "cells": [{**one_row["cells"][0], "colspan": 2}, *one_row["cells"][1:]]}
    outcomes = _ok("edit_draft", draft, {"ops": [eight, overlapping, one_row]}, context)["outcomes"]
    assert [(o["accepted"], o.get("rule")) for o in outcomes] == [(False, "structure_only"), (False, "cell"), (True, None)]
    assert "| 项目 | 数值 | SENTINEL-OCR 甲 | 3 |" in _markdown(draft)
    changes = _ok("read_draft", draft, {"view": "changes"}, context)["changes"]
    assert [(c["op"], c["target"], c["what"]) for c in changes] == [("set_table", table, "1×4")]
