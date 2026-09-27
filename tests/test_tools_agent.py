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
        {"op": "set_role", "block": text, "kind": "title", "level": 3, "reason": "小节名"},
        {"op": "mark_pending", "block": title, "reason": "拿不准"},
        {"op": "set_level", "block": text, "level": 5, "reason": "跳级"},
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
        {"op": "set_role", "block": text, "kind": "title", "level": 3, "reason": "r"},
        {"op": "set_level", "block": text, "level": 6, "reason": "跳级"},
    ]}, context)
    assert [o["accepted"] for o in result["outcomes"]] == [False, False] and _markdown(draft) == before


def test_links_and_exclusion_are_reversible(draft, context):
    first, second = _block(draft, "SENTINEL-NATIVE 采购"), _block(draft, "扫描文字")
    ops = [{"op": "link", "kind": "continues", "src": first, "dst": second, "reason": "跨页续接"},
           {"op": "exclude", "block": second, "reason": "r"}]
    assert all(o["accepted"] for o in _ok("edit_draft", draft, {"ops": ops}, context)["outcomes"])
    assert "扫描文字" not in _markdown(draft)
    back = [{"op": "include", "block": second, "reason": "r"},
            {"op": "unlink", "kind": "continues", "src": first, "dst": second, "reason": "r"}]
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
    assert "SENTINEL-OCR 扫描文字" in look["text"]["doc_text"]
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
    done = _ok("submit_draft", ws, {"out": str(tmp_path / "out")}, context)
    assert done["accepted"] and done["markdown"].endswith(".md") and (tmp_path / "out").is_dir()


def test_the_agent_sees_only_its_four_tools():
    from parserx.tools import AGENT_TOOLS

    assert AGENT_TOOLS == ("read_draft", "view_source", "edit_draft", "submit_draft")
