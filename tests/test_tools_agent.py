"""The agent's four tools (Q85): read_draft, view_source, edit_draft, submit_draft.

The draft is read and changed only through them; looking at the source leaves evidence and never changes the
draft; a change that rests on the source cites its evidence.
"""

import pytest

from parserx.render import render_markdown
from parserx.tools import call_tool
from parserx.workspace import Workspace
from tests.test_tools_contract import _assert_contract, _config, _context, pdf, ws  # noqa: F401  (fixtures)


def _run(name, ws, request, context):
    env, code = call_tool(name, ws, request, config=_config(), context_factory=context)
    return env, code


def _ok(name, ws, request, context):
    env, code = _run(name, ws, request, context)
    assert code == 0 and env.ok, env.failures
    return _assert_contract(env, name)["result"]


def _markdown(ws):
    return render_markdown(Workspace.open(ws).load())


@pytest.fixture
def context():
    return _context()


@pytest.fixture
def draft(ws, context):
    """The workspace after the pipeline: the draft the agent starts from."""
    env, _ = call_tool("run_pipeline", ws, {}, config=_config(), context_factory=context)
    assert env.ok, env.failures
    return ws


def _block(ws, text):
    return next(b.id for b in Workspace.open(ws).load().blocks if text in (b.text or "") or (b.cells and any(
        text in c.content for c in b.cells.cells)))


# ── read_draft ──────────────────────────────────────────────────────────


def test_read_draft_summary_text_search_and_blocks(draft, context):
    summary = _ok("read_draft", draft, {}, context)["summary"]
    assert [p["status"] for p in summary["pages"]] == ["done", "done"] and summary["blocks"]["table"] == 1

    text = _ok("read_draft", draft, {"view": "text", "after": 3}, context)
    assert [line["role"] for line in text["lines"]] == ["H1", "text", "H2"] and text["after_id"]
    rest = _ok("read_draft", draft, {"view": "text", "start": text["after_id"]}, context)
    assert rest["lines"][0]["text"]["doc_text"].startswith("SENTINEL-OCR 扫描文字")

    found = _ok("read_draft", draft, {"view": "text", "find": "扫描文字3件"}, context)  # spacing does not matter
    assert [line["id"] for line in found["lines"]] == [_block(draft, "扫描文字")]
    in_table = _ok("read_draft", draft, {"view": "text", "pattern": r"甲\s*\|\s*\d"}, context)  # a cell's row
    assert in_table["lines"][0]["id"] == _block(draft, "甲") and in_table["lines"][0]["row"] == 1

    table = _ok("read_draft", draft, {"view": "blocks", "blocks": [_block(draft, "甲")]}, context)["blocks"][0]
    assert table["table"]["n_rows"] == 2 and any(c["content"]["doc_text"] == "3" for c in table["table"]["cells"])


def test_read_draft_outline_shows_the_style_classes(draft, context):
    outline = _ok("read_draft", draft, {"view": "outline"}, context)
    assert outline["classes"] and {line["role"] for line in outline["lines"]} >= {"H1", "H2"}


# ── view_source: evidence, never a change of the draft ──────────────────


def test_looking_at_the_source_leaves_evidence_and_keeps_the_draft(draft, context):
    before = _markdown(draft)
    table = _block(draft, "甲")
    looks = _ok("view_source", draft, {"looks": [
        {"block": table, "as": "image"},
        {"block": table, "as": "answer", "question": "第 1 行第 1 列是什么？"},
        {"page": 2, "as": "answer", "question": "页首是什么？"},
    ]}, context)["results"]
    assert looks[0]["image"]["path"].endswith(".png") and looks[1]["answer"]["doc_text"].startswith("SENTINEL-VLM")
    assert all(look["evidence"].startswith("e-") for look in looks) and len({x["evidence"] for x in looks}) == 3
    assert _markdown(draft) == before


# ── edit_draft ──────────────────────────────────────────────────────────


def test_a_content_change_needs_evidence_of_its_place(draft, context):
    table, text = _block(draft, "甲"), _block(draft, "扫描文字")
    ops = [{"op": "set_cells", "block": table, "cells": [{"row": 1, "col": 1, "content": "8"}],
            "reason": "图上是 8", "evidence": "e-000000000000"}]
    refused = _ok("edit_draft", draft, {"ops": ops}, context)["outcomes"][0]
    assert not refused["accepted"] and refused["rule"] == "image_evidence"

    evidence = _ok("view_source", draft, {"looks": [{"block": table, "as": "answer", "question": "?"}]},
                   context)["results"][0]["evidence"]
    ops[0]["evidence"] = evidence
    ops.append({"op": "replace_text", "block": text, "find": "3 件", "replace": "8 件", "reason": "图上是 8",
                "evidence": evidence})  # the table's crop is no evidence for the text above it
    outcomes = _ok("edit_draft", draft, {"ops": ops}, context)["outcomes"]
    assert [o["accepted"] for o in outcomes] == [True, False] and outcomes[1]["rule"] == "image_evidence"
    assert "| SENTINEL-OCR 甲 | 8 |" in _markdown(draft)


def test_the_changes_view_lists_what_was_accepted_in_order(draft, context):
    text, title = _block(draft, "扫描文字"), _block(draft, "SENTINEL-OCR 标题")
    evidence = _ok("view_source", draft, {"looks": [{"page": 2, "as": "image"}]}, context)["results"][0]["evidence"]
    _ok("edit_draft", draft, {"ops": [
        {"op": "replace_text", "block": text, "find": "3 件", "replace": "8 件", "reason": "图上是 8", "evidence": evidence},
        {"op": "set_role", "block": text, "role": "H5", "reason": "refused: a skipped level"},
        {"op": "exclude", "block": title, "reason": "扫描软件字样"}]}, context)
    changes = _ok("read_draft", draft, {"view": "changes"}, context)["changes"]
    assert [(c["op"], c["target"], c["reason"]) for c in changes] == [
        ("replace_text", text, "图上是 8"), ("exclude", title, "扫描软件字样")]
    assert changes[0]["text"]["doc_text"] == "3 件 → 8 件" and changes[0]["evidence"] == evidence


def test_notes_are_written_revised_and_read_back(draft, context):
    evidence = _ok("view_source", draft, {"looks": [{"page": 2, "as": "image"}]}, context)["results"][0]["evidence"]
    first = _ok("edit_draft", draft, {"ops": [{"op": "note", "scope": "第 2 页", "text": "扫描页，正文一段",
                                               "evidence": [evidence]}]}, context)["outcomes"][0]
    assert first["accepted"] and first["target"] == "n-001"
    outcomes = _ok("edit_draft", draft, {"ops": [
        {"op": "note", "scope": "第 2 页", "text": "扫描页：一个标题与一段正文", "replaces": "n-001"},
        {"op": "note", "scope": "全文", "text": "x", "replaces": "n-001"},  # n-001 is no longer current
        {"op": "note", "scope": "全文", "text": "y", "evidence": ["e-000000000000"]}]}, context)["outcomes"]
    assert [(o["accepted"], o.get("rule")) for o in outcomes] == [(True, None), (False, "unknown_note"),
                                                                  (False, "evidence")]
    notes = _ok("read_draft", draft, {"view": "notes"}, context)["notes"]
    assert [(n["id"], n["text"], n["replaces"]) for n in notes] == [("n-002", "扫描页：一个标题与一段正文", "n-001")]
    assert _ok("read_draft", draft, {}, context)["summary"]["notes"] == 1
    changes = _ok("read_draft", draft, {"view": "changes"}, context)["changes"]
    assert [c["target"] for c in changes if c["op"] == "note"] == ["n-001", "n-002"]
    assert len(Workspace.open(draft).load().notes) == 2  # the history stays, in the state and so in the sidecar


def test_the_same_misreading_is_replaced_everywhere_in_a_block_when_asked(draft, context):
    text = _block(draft, "扫描文字")
    evidence = _ok("view_source", draft, {"looks": [{"page": 2, "as": "image"}]}, context)["results"][0]["evidence"]
    op = {"op": "replace_text", "block": text, "find": "N", "replace": "Z", "reason": "r", "evidence": evidence}
    once = _ok("edit_draft", draft, {"ops": [op]}, context)["outcomes"][0]
    assert not once["accepted"] and "all: true" in once["detail"]
    assert _ok("edit_draft", draft, {"ops": [{**op, "all": True}]}, context)["outcomes"][0]["accepted"]


def test_find_must_name_one_place(draft, context):
    text = _block(draft, "扫描文字")
    evidence = _ok("view_source", draft, {"looks": [{"page": 2, "as": "image"}]}, context)["results"][0]["evidence"]
    outcome = _ok("edit_draft", draft, {"ops": [{"op": "replace_text", "block": text, "find": "不存在",
                                                 "replace": "x", "reason": "r", "evidence": evidence}]},
                  context)["outcomes"][0]
    assert not outcome["accepted"] and "exactly once" in outcome["detail"]


def test_structure_ops_and_dismissing_an_issue(draft, context):
    text, title = _block(draft, "扫描文字"), _block(draft, "SENTINEL-OCR 标题")
    result = _ok("edit_draft", draft, {"ops": [
        {"op": "set_role", "block": text, "role": "H3", "reason": "小节名"},
        {"op": "mark_pending", "block": title, "reason": "拿不准"},
        {"op": "set_role", "block": text, "role": "H5", "reason": "跳级"},
    ]}, context)
    assert [o["accepted"] for o in result["outcomes"]] == [True, True, False]
    assert result["outcomes"][2]["rule"] == "level_skip"
    opened = [i for i in result["issues_opened"] if i["kind"] == "structure_pending"]
    assert opened and opened[0]["id"].startswith("w-")

    issues = _ok("read_draft", draft, {"view": "issues"}, context)["issues"]
    assert opened[0]["id"] in {i["id"] for i in issues}
    evidence = _ok("view_source", draft, {"looks": [{"block": title, "as": "image"}]}, context)["results"][0]
    done = _ok("edit_draft", draft, {"ops": [{"op": "dismiss", "issue": opened[0]["id"], "reason": "看过，待定合理",
                                              "evidence": evidence["evidence"]}]}, context)
    assert done["outcomes"][0]["accepted"] and done["issues_closed"] == [opened[0]["id"]]
    assert opened[0]["id"] not in {i["id"] for i in _ok("read_draft", draft, {"view": "issues"}, context)["issues"]}


def test_atomic_edits_apply_all_or_nothing(draft, context):
    text = _block(draft, "扫描文字")
    before = _markdown(draft)
    result = _ok("edit_draft", draft, {"atomic": True, "ops": [
        {"op": "set_role", "block": text, "role": "H3", "reason": "r"},
        {"op": "set_role", "block": text, "role": "H6", "reason": "跳级"},
    ]}, context)
    assert [o["accepted"] for o in result["outcomes"]] == [False, False] and _markdown(draft) == before


def test_joins_and_exclusion_are_reversible(draft, context):
    first, second = _block(draft, "SENTINEL-NATIVE 采购"), _block(draft, "扫描文字")
    ops = [{"op": "join", "first": first, "second": second, "reason": "跨页续接"},
           {"op": "exclude", "block": second, "reason": "r"}]
    assert all(o["accepted"] for o in _ok("edit_draft", draft, {"ops": ops}, context)["outcomes"])
    assert "扫描文字" not in _markdown(draft)
    back = [{"op": "include", "block": second, "reason": "r"},
            {"op": "unjoin", "first": first, "second": second, "reason": "r"}]
    assert all(o["accepted"] for o in _ok("edit_draft", draft, {"ops": back}, context)["outcomes"])
    assert "扫描文字" in _markdown(draft)


# ── reading the source with an engine, then adopting the reading ────────


def test_a_table_reading_is_adopted_through_the_gate(draft, context):
    table = _block(draft, "甲")
    look = _ok("view_source", draft, {"looks": [{"block": table, "as": "table", "issues": [
        {"kind": "char", "cells": [[1, 1]], "note": "3 还是 8？"}]}]}, context)["results"][0]
    assert look["table"]["n_rows"] == 2 and "| SENTINEL-OCR 甲 | 3 |" in _markdown(draft)  # not adopted yet
    outcome = _ok("edit_draft", draft, {"ops": [{"op": "adopt", "block": table, "evidence": look["evidence"],
                                                 "reason": "重读的表格"}]}, context)["outcomes"][0]
    assert outcome["accepted"] and "| SENTINEL-OCR 甲 | 8 |" in _markdown(draft)


def test_a_description_is_adopted(draft, context):
    figure = next(b.id for b in Workspace.open(draft).load().blocks if b.kind.value == "figure")
    look = _ok("view_source", draft, {"looks": [{"block": figure, "as": "description"}]}, context)["results"][0]
    assert look["description"]["doc_text"]
    outcome = _ok("edit_draft", draft, {"ops": [{"op": "adopt", "block": figure, "evidence": look["evidence"],
                                                 "reason": "图有内容"}]}, context)["outcomes"][0]
    assert outcome["accepted"]
    assert next(b for b in Workspace.open(draft).load().blocks if b.id == figure).semantic is not None


def test_a_page_the_draft_lacks_is_read_and_adopted(ws, context):
    # before the pipeline: page 2 is a scan with nothing read yet
    look = _ok("view_source", ws, {"looks": [{"page": 2, "as": "text"}]}, context)["results"][0]
    assert any("SENTINEL-OCR 扫描文字" in (b.get("text") or {}).get("doc_text", "") for b in look["reading"])
    assert _ok("read_draft", ws, {}, context)["summary"]["pages"][1]["status"] == "pending"
    outcome = _ok("edit_draft", ws, {"ops": [{"op": "adopt", "page": 2, "evidence": look["evidence"],
                                              "reason": "扫描页"}]}, context)["outcomes"][0]
    assert outcome["accepted"] and "SENTINEL-OCR 扫描文字" in _markdown(ws)


def test_an_adopted_reading_must_be_of_that_place(draft, context):
    table, text = _block(draft, "甲"), _block(draft, "扫描文字")
    look = _ok("view_source", draft, {"looks": [{"block": table, "as": "table", "issues": [
        {"kind": "char", "cells": [[1, 1]], "note": "?"}]}]}, context)["results"][0]
    outcome = _ok("edit_draft", draft, {"ops": [{"op": "adopt", "block": text, "evidence": look["evidence"],
                                                 "reason": "r"}]}, context)["outcomes"][0]
    assert not outcome["accepted"] and outcome["rule"] == "evidence_target"


# ── submit_draft ────────────────────────────────────────────────────────


def test_submit_refuses_an_unfinished_draft_and_exports_a_finished_one(ws, context, tmp_path):
    refused = _ok("submit_draft", ws, {}, context)
    assert not refused["accepted"] and any("pending" in b for b in refused["blockers"])
    call_tool("run_pipeline", ws, {}, config=_config(), context_factory=context)
    assert _ok("submit_draft", ws, {}, context)["accepted"]
    done = _ok("export", ws, {"out": str(tmp_path / "out")}, context)  # the program's step, after the agent
    assert done["accepted"] and done["markdown"].endswith(".md") and (tmp_path / "out").is_dir()


def test_the_agent_sees_only_its_four_tools():
    from parserx.tools import AGENT_TOOLS

    assert AGENT_TOOLS == ("read_draft", "view_source", "edit_draft", "submit_draft")


# ── read_draft as a person skims: scroll, a page, a search, the outline ─

BODY = ("SENTINEL-NATIVE body text of the section, long enough to be a paragraph of its own and to be shortened "
        "when the reader only glances at it, as a person does when skimming.")


@pytest.fixture
def report(tmp_path):
    import pymupdf

    from parserx.tools import workspace_init

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for text, size, font in [("SENTINEL-NATIVE Annual Report", 18, "hebo"), ("1 Scope", 12, "hebo"),
                             (BODY, 10, "helv"), ("1.1 Terms", 12, "hebo"), (BODY, 10, "helv")]:
        page.insert_textbox(pymupdf.Rect(72, y, 523, y + 60), text, fontsize=size, fontname=font)
        y += 70
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 80, 523, 140), "2 Methods", fontsize=12, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 150, 523, 210), BODY, fontsize=10, fontname="helv")
    doc.save(tmp_path / "report.pdf")
    envelope, code = workspace_init(tmp_path / "report.pdf", tmp_path / "rws", config=_config())
    assert code == 0 and envelope.ok
    return tmp_path / "rws"


def _texts(lines):
    return [(line["text"] or {}).get("doc_text", "") for line in lines]


def test_scroll_from_the_start_and_on_from_where_it_stopped(report, context):
    first = _ok("read_draft", report, {"view": "text", "after": 3}, context)
    assert len(first["lines"]) == 3 and first["total_blocks"] == 7
    assert _texts(first["lines"])[:2] == ["SENTINEL-NATIVE Annual Report", "1 Scope"]
    body = first["lines"][2]
    assert body["text"]["doc_text"].endswith("…") and len(body["text"]["doc_text"]) < len(BODY)  # a glance
    assert all(line["page"] == 1 and line["cls"] for line in first["lines"])
    on = _ok("read_draft", report, {"view": "text", "start": first["after_id"], "after": 10}, context)
    assert _texts(on["lines"])[0] == "1.1 Terms" and "after_id" not in on  # the end of the document
    back = _ok("read_draft", report, {"view": "text", "start": on["lines"][0]["id"], "before": 2, "after": 0}, context)
    assert [line["id"] for line in back["lines"]] == [line["id"] for line in first["lines"][1:]]
    full = _ok("read_draft", report, {"view": "text", "start": body["id"], "after": 1, "full": True}, context)
    assert [" ".join(t.split()) for t in _texts(full["lines"])] == [BODY]  # the block's text, line breaks kept


def test_a_page_and_a_phrase(report, context):
    page2 = _ok("read_draft", report, {"view": "text", "page": 2}, context)
    assert _texts(page2["lines"])[0] == "2 Methods"
    found = _ok("read_draft", report, {"view": "text", "find": "1.1  terms"}, context)  # spacing and case ignored
    assert _texts(found["lines"]) == ["1.1 Terms"]


def test_the_outline_shows_the_documents_conventions(report, context):
    outline = _ok("read_draft", report, {"view": "outline"}, context)
    classes = {c["id"]: c for c in outline["classes"]}
    heads = [line for line in outline["lines"] if _texts([line])[0] in ("1 Scope", "1.1 Terms", "2 Methods")]
    assert len(heads) == 3  # every heading-like line of the document, with what follows it
    assert all(line["next"] and "body text" in line["next"]["doc_text"] for line in heads)
    scope = classes[heads[0]["cls"]]
    assert scope["numbering"] == "N" and scope["count"] == 2 and scope["pages"] == "1–2"  # "1 Scope", "2 Methods"
    body = next(c for c in outline["classes"] if c["count"] == 3)  # the body text: one class, never a title
    assert body["roles"] == {"text": 3} and body["chars"] > 100 and body["examples"]
    every = _ok("read_draft", report, {"view": "text", "cls": body["id"]}, context)
    assert len(every["lines"]) == 3


def test_one_way_of_reading_at_a_time(report, context):
    env, code = _run("read_draft", report, {"view": "text", "page": 1, "find": "x"}, context)
    assert code == 2 and not env.ok
    env, code = _run("read_draft", report, {"view": "summary", "find": "x"}, context)
    assert code == 2 and not env.ok


def test_an_outline_is_relevelled_in_one_call(report, context):
    # "1 Scope" / "2 Methods" and "1.1 Terms" moved up together: each move alone would skip a level
    assert _ok("run_pipeline", report, {}, context)
    lines = {(line["text"] or {}).get("doc_text"): line
             for line in _ok("read_draft", report, {"view": "outline"}, context)["lines"]}
    scope, terms, methods = (lines[t]["id"] for t in ("1 Scope", "1.1 Terms", "2 Methods"))
    before = {lines[t]["role"] for t in ("1 Scope", "2 Methods")}
    assert before == {"H2"}  # under the report's title
    ops = [{"op": "set_role", "block": b, "role": f"H{lv}", "reason": "no document title above them"}
           for b, lv in ((scope, 1), (terms, 2), (methods, 1))]
    title = lines["SENTINEL-NATIVE Annual Report"]["id"]
    ops.insert(0, {"op": "set_role", "block": title, "role": "text", "reason": "a cover line"})
    outcomes = _ok("edit_draft", report, {"ops": ops}, context)["outcomes"]
    assert all(o["accepted"] for o in outcomes), outcomes
    roles = {line["id"]: line["role"] for line in _ok("read_draft", report, {"view": "outline"}, context)["lines"]}
    assert (roles[scope], roles[terms], roles[methods]) == ("H1", "H2", "H1")


def test_a_look_at_a_region_is_evidence_for_the_blocks_in_it(draft, context):
    state = Workspace.open(draft).load()
    block = next(b for b in state.blocks if (b.text or "").startswith("SENTINEL-NATIVE 采购"))
    x0, y0, x1, y1 = block.anchors[0].bbox
    around = _ok("view_source", draft, {"looks": [{"page": 1, "bbox": [x0 - 5, y0 - 5, x1 + 5, y1 + 5]}]},
                 context)["results"][0]["evidence"]
    elsewhere = _ok("view_source", draft, {"looks": [{"page": 1, "bbox": [72, 600, 300, 700]}]},
                    context)["results"][0]["evidence"]
    edit = {"op": "replace_text", "block": block.id, "find": "采购", "replace": "采买", "reason": "r"}
    assert not _ok("edit_draft", draft, {"ops": [{**edit, "evidence": elsewhere}]}, context)["outcomes"][0]["accepted"]
    assert _ok("edit_draft", draft, {"ops": [{**edit, "evidence": around}]}, context)["outcomes"][0]["accepted"]


# ── a region read again: the draft's blocks replaced, content conserved, reversible (Q87) ──


def _rows_as_text(tmp_path):
    """A native page whose table has no ruling: the draft has lines of text where the page shows a table."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for i, row in enumerate(("项目 数值", "甲 3", "乙 8")):
        page.insert_text((72, 100 + 40 * i), row, fontsize=12, fontname="china-s")
    doc.save(tmp_path / "rows.pdf")
    from parserx.tools import workspace_init

    workspace_init(tmp_path / "rows.pdf", tmp_path / "rws", config=_config())
    return tmp_path / "rws"


def _table_reading(value):
    html = f"<table><tr><td>项目</td><td>数值</td></tr><tr><td>甲</td><td>3</td></tr><tr><td>乙</td><td>{value}</td></tr></table>"
    return lambda: {"prunedResult": {"width": 600, "height": 200, "parsing_res_list": [
        {"block_label": "table", "block_content": html, "block_bbox": [10, 10, 590, 190], "block_order": 1}]}}


def test_a_region_read_again_replaces_its_blocks_and_can_be_undone(tmp_path):
    ws = _rows_as_text(tmp_path)
    before = _markdown(ws)
    region = {"page": 1, "bbox": [60, 80, 300, 190], "as": "text"}
    look = _ok("view_source", ws, {"looks": [region]}, _context(page=_table_reading(8)))["results"][0]
    assert look["reading"][0]["kind"] == "table"
    outcome = _ok("edit_draft", ws, {"ops": [{"op": "adopt", "page": 1, "evidence": look["evidence"],
                                              "reason": "原件是一张表"}]}, _context())["outcomes"][0]
    assert outcome["accepted"], outcome
    assert "| 甲 | 3 |" in _markdown(ws) and "甲 3" not in _markdown(ws)
    assert _ok("submit_draft", ws, {}, _context())["accepted"]  # the accounts balance
    undo = _ok("edit_draft", ws, {"ops": [{"op": "unadopt", "evidence": look["evidence"], "reason": "撤回"}]},
               _context())["outcomes"][0]
    assert undo["accepted"] and _markdown(ws) == before and _ok("submit_draft", ws, {}, _context())["accepted"]


def test_a_region_reading_that_changes_native_letters_is_adopted_and_recorded(tmp_path):
    # user 2026-09-30, keep the original as printed: a native letter the reading changes is a signal like a number
    from parserx.render.summary import document_summary
    from parserx.workspace import Workspace

    ws = _rows_as_text(tmp_path)
    html = "<table><tr><td>项目</td><td>数值</td></tr><tr><td>申</td><td>3</td></tr><tr><td>乙</td><td>8</td></tr></table>"
    reading = lambda: {"prunedResult": {"width": 600, "height": 200, "parsing_res_list": [  # noqa: E731
        {"block_label": "table", "block_content": html, "block_bbox": [10, 10, 590, 190], "block_order": 1}]}}
    region = {"page": 1, "bbox": [60, 80, 300, 190], "as": "text"}
    look = _ok("view_source", ws, {"looks": [region]}, _context(page=reading))["results"][0]
    outcome = _ok("edit_draft", ws, {"ops": [{"op": "adopt", "page": 1, "evidence": look["evidence"],
                                              "reason": "原件是一张表"}]}, _context())["outcomes"][0]
    assert outcome["accepted"] and "lost '甲', added '申'" in outcome["detail"]
    listed = document_summary(Workspace.open(ws).load(), "doc").review.agent_overrides
    assert len(listed) == 1 and "native_text_changed" in listed[0].signals


def test_a_region_reading_that_changes_native_numbers_is_adopted_and_recorded(tmp_path):
    # execution plan §3.4: the agent decides; the lost number is a signal on the record, and unadopt brings it back
    from parserx.render.summary import document_summary
    from parserx.workspace import Workspace

    ws = _rows_as_text(tmp_path)
    before = _markdown(ws)
    region = {"page": 1, "bbox": [60, 80, 300, 190], "as": "text"}
    look = _ok("view_source", ws, {"looks": [region]}, _context(page=_table_reading(6)))["results"][0]  # 8 → 6
    outcome = _ok("edit_draft", ws, {"ops": [{"op": "adopt", "page": 1, "evidence": look["evidence"],
                                              "reason": "原件是 6"}]}, _context())["outcomes"][0]
    assert outcome["accepted"] and "native numbers" in outcome["detail"] and "8" in outcome["detail"]
    listed = document_summary(Workspace.open(ws).load(), "doc").review.agent_overrides
    assert len(listed) == 1 and "region_numbers_changed" in listed[0].signals and "原件是 6" in listed[0].reason
    undo = _ok("edit_draft", ws, {"ops": [{"op": "unadopt", "evidence": look["evidence"], "reason": "撤回"}]},
               _context())["outcomes"][0]
    assert undo["accepted"] and _markdown(ws) == before
