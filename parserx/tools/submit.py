"""``submit_draft``: hand in the draft (Q85); ``export``: write the output package (the program's step, Q86).

The program checks the accounts: every page has a status, every piece of content found in the source has its
destination, no reference points nowhere, no asset is lost.  When they balance the draft is accepted.  When they do
not, the answer names what stands in the way; the agent fixes it and submits again.  Open issues are counted, not
blocking: an issue left open is reported as open.  Exporting is not the agent's: the program exports an accepted
draft after the agent (or after the pipeline) with ``export``, which checks the same way first.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from parserx.ir.base import IRModel
from parserx.ir.enums import DocumentStatus
from parserx.render import write_export
from parserx.accounting import CheckResult, check
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.views import unresolved_items

DESCRIPTION = ("交稿：程序核对账目（每页都有状态，原件中找到的每处内容都有去向，没有指向不存在之处的引用，没有丢失的图片）。"
               "平衡即被接受（accepted 为 true），任务完成；否则 blockers 说明原因，处理后再交。"
               "仍开着的待办只报告（open_issues），不阻止交稿。不导出：程序在你结束后导出。")


class SubmitDraftRequest(IRModel):
    pass


class SubmitDraftResult(IRModel):
    accepted: bool
    status: DocumentStatus  # complete / partial / failed
    blockers: list[str]  # what keeps the draft from being accepted
    open_issues: dict[str, int]  # issues still open, by kind (reported, not blocking)


class ExportRequest(IRModel):
    out: str  # the directory: <name>.md, <name>.json, <name>.blocks.json, images/
    name: str | None = None  # default: the document id


class ExportResult(SubmitDraftResult):
    markdown: str | None = None  # the files written, when accepted
    sidecar: str | None = None
    summary: str | None = None


def checked(ctx: ToolContext) -> CheckResult:
    """Run the accounting check and record the document's status and missing items when they changed."""
    state = ctx.ws.load()
    result = check(state, ctx.ws.root)
    if state.status != result.document_status or state.missing != result.missing:
        with ctx.ws.txn("tool:check") as state:
            state.status = result.document_status
            state.missing = result.missing
    return result


def _submitted(ctx: ToolContext) -> SubmitDraftResult:
    result = checked(ctx)
    state = ctx.ws.load()
    open_issues = dict(sorted(Counter(u.kind.value for u in unresolved_items(state)).items()))
    blockers = []
    pending = [p.n for p in result.pages if p.status.value == "pending"]
    if pending:
        blockers.append(f"pending pages {pending}: nothing is read from them yet (view_source as text, then adopt)")
    for what, items in (("unassigned ledger items", result.unassigned), ("mismatched ledger items", result.mismatched),
                        ("illegal references", result.illegal_refs), ("missing assets", result.missing_assets)):
        if items:
            blockers.append(f"{len(items)} {what}")
    return SubmitDraftResult(accepted=result.exportable and not blockers, status=state.status, blockers=blockers,
                             open_issues=open_issues)


def run(ctx: ToolContext, req: SubmitDraftRequest) -> ToolOutput[SubmitDraftResult]:
    return output(_submitted(ctx))


def export(ctx: ToolContext, req: ExportRequest) -> ToolOutput[ExportResult]:
    exported = ExportResult(**_submitted(ctx).model_dump())
    if exported.accepted:
        state = ctx.ws.load()
        paths = write_export(state, ctx.ws.root, Path(req.out), req.name or state.id, lang=ctx.config.output.lang)
        exported.markdown, exported.sidecar = str(paths.markdown.resolve()), str(paths.sidecar.resolve())
        exported.summary = str(paths.summary.resolve())
    return output(exported)
