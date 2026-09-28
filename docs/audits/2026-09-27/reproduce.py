"""Offline audit probes, not acceptance tests. Run from the repository with uv run python.

All source text/evidence is synthetic. No OCR, VLM or Agent service is called.
Outputs describe current behavior; they do not assert that behavior is correct.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.content.select import review_table
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, ObservationStatus, PageStatus, TaskKind
from parserx.ir.evidence import Evidence
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState, LedgerEntry, PageReading, PageState, ReadLine
from parserx.reading.compare import unaccounted_lines
from parserx.scheduling.budget import Budget, BudgetLimits
from parserx.tables import Cell, TableGrid
from parserx.tools import call_tool
from parserx.tools.evidence import image_evidence
from parserx.workspace import Workspace


def anchor(y=10):
    return PdfAnchor(page=1, bbox=(10, y, 200, y + 20), coord_space="page_pt")


def block(text, block_id="b-p001-0001", order=0):
    obs = Observation(id=f"o-{block_id}", engine="native_pdf", engine_version="fixture",
                      task=TaskKind.EXTRACT, anchor=anchor(), text=text, status=ObservationStatus.OK)
    return Block(id=block_id, kind=BlockKind.TEXT, order=order, anchors=[anchor()],
                 text=text, observations=[obs], chosen_observation=obs.id)


def state_with(*blocks):
    return DocumentState(id="audit", source="fixture.pdf", source_sha256="0" * 64, format="pdf",
                         status=DocumentStatus.IN_PROGRESS,
                         pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE)],
                         blocks=list(blocks),
                         evidence=[Evidence(id="e-fixture", how="answer", page=1,
                                            question="What does the source say?", answer="采购金额为100万元，不允许延期。")],
                         ledger=[LedgerEntry(item=f"i-{b.id}", unit="native_line", source=anchor(),
                                             chars=len(b.text), disposition="output", block=b.id) for b in blocks])


def tool_probes():
    config = ParserXConfig()
    config.cache.mode = "off"
    with tempfile.TemporaryDirectory(prefix="parserx-audit-") as scratch:
        root = Path(scratch)
        source = root / "fixture.pdf"
        source.write_bytes(b"synthetic fixture, tools only; never opened as PDF")

        def workspace(name, state):
            state.source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            ws = Workspace.create(root / name, state, source)
            ws.log_call({"tool": "fixture_init"})
            return ws

        def edit(ws, find, replace):
            env, code = call_tool("edit_draft", ws.root, {"ops": [{"op": "replace_text",
                                  "block": "b-p001-0001", "find": find, "replace": replace,
                                  "evidence": "e-fixture", "reason": "synthetic audit proposal"}]}, config=config)
            return {"exit_code": code, "ok": env.ok, "result": env.result.model_dump(mode="json") if env.result else None,
                    "text_after": ws.load().blocks[0].text}

        direct = workspace("direct", state_with(block("采购金额为100万元，不允许延期。")))
        sequence = workspace("sequence", state_with(block("采购金额为100万元，不允许延期。")))
        direct_result = edit(direct, "100", "999")
        first = edit(sequence, "采购金额为", "采购金额：")
        second = edit(sequence, "100", "999")

        semantic = workspace("semantic", state_with(block("采购金额为100万元，不允许延期。")))
        negate = edit(semantic, "不允许", "允许")

        excluded = workspace("exclude", state_with(block("普通段落。"), block("关键免责条款。", "b-p001-0002", 1)))
        env, _ = call_tool("edit_draft", excluded.root, {"ops": [{"op": "exclude", "block": "b-p001-0002",
                           "reason": "synthetic unsupported exclusion"}]}, config=config)
        submitted, _ = call_tool("submit_draft", excluded.root, {}, config=config)
        return {"direct_native_number_edit": direct_result,
                "native_protection_after_accepted_edit": {"first": first, "second": second},
                "negation_removed": negate,
                "exclude_without_evidence": {"edit": env.result.model_dump(mode="json"),
                                               "submit": submitted.result.model_dump(mode="json")}}


def grid(rows):
    return TableGrid(n_rows=len(rows), n_cols=len(rows[0]),
                     cells=[Cell(row=r, col=c, content=s) for r, row in enumerate(rows) for c, s in enumerate(row)])


def table_probe():
    before = grid([["项目", "数值"], ["甲", "3"], ["乙", "20"]])
    after = grid([["项目", "数值"], ["甲", "20"], ["乙", "3"]])
    obs = Observation(id="o-native", engine="native_pdf", engine_version="fixture", task=TaskKind.EXTRACT,
                      anchor=anchor(), cells=before, status=ObservationStatus.OK)
    b = Block(id="b-table", kind=BlockKind.TABLE, order=0, anchors=[anchor()], cells=before,
              observations=[obs], chosen_observation=obs.id)
    candidate = Observation(id="o-candidate", engine="vlm", engine_version="fixture", task=TaskKind.REVIEW,
                            raw_ref="synthetic-recorded-image-response",
                            anchor=AssetAnchor(asset="a-crop", bbox=(0, 0, 100, 100), image_size=(100, 100)),
                            cells=after, status=ObservationStatus.OK)
    result = review_table(b, candidate, allowed_cells=set(), actor="audit")
    return {"adopted": result.adopted, "gate": [g.model_dump() for g in result.gate],
            "after": [[b.cells.slot(r, c).content for c in range(2)] for r in range(3)]}


def scope_probes():
    b = block("top of page", order=0)
    b.anchors = [anchor(10)]
    state = state_with(b)
    state.evidence += [Evidence(id="e-seam", how="image", seam=1),
                       Evidence(id="e-tiny", how="image", page=1, bbox=(199, 10, 201, 11))]
    return {ref: image_evidence(state, b, ref).model_dump() for ref in ("e-seam", "e-tiny")}


def budget_probe():
    budget = Budget(BudgetLimits(usd=0.02, reserve_usd={"vlm": 0.01}))
    reservation = budget.reserve("vlm")
    budget.settle(reservation, 0.20)
    return {"configured_cap": 0.02, "settled_single_request": 0.20, "left": budget.left(),
            "interpretation": "reservation is an estimate, not a bound on actual request cost"}


def missing_repeated_body_probe():
    state = state_with(block("已提取的一行。"))
    state.pages.append(PageState(n=2, unit="pdf_page", status=PageStatus.DONE))
    state.readings = [PageReading(n=n, engine="synthetic-independent-reading", dpi=100,
                                 lines=[ReadLine(bbox=(50, 300, 450, 320), text="必须在加载前锁紧安全装置", score=0.99)])
                      for n in (1, 2)]
    repeated = {str(n): [ln.text for ln in lines] for n, lines in unaccounted_lines(state).items()}
    state.readings = state.readings[:1]
    single = {str(n): [ln.text for ln in lines] for n, lines in unaccounted_lines(state).items()}
    return {"single_page_missing_detected": single, "same_missing_body_on_two_pages": repeated}


def metadata():
    root = Path(__file__).resolve().parents[3]
    paths = ["parserx/content/select.py", "parserx/tools/edit.py", "parserx/tools/evidence.py",
             "parserx/accounting/check.py", "parserx/reading/compare.py", "parserx/scheduling/budget.py",
             "parserx/runtimes/hybrid.py", "parserx/hierarchy/legality.py"]
    return {"at": datetime.now().astimezone().isoformat(),
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
            "files_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
            "scope": "Synthetic offline probes on the working tree; no live services; not corpus quality scores"}


if __name__ == "__main__":
    result = {"metadata": metadata(), "tools": tool_probes(), "swapped_native_cells": table_probe(),
              "evidence_scope": scope_probes(), "budget": budget_probe(),
              "repeated_missing_body": missing_repeated_body_probe()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
