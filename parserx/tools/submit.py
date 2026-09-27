"""``submit_draft``: hand in the draft (Q85).

The program checks the accounts: every page has a status, every piece of content found in the source has its
destination, no reference points nowhere, no asset is lost.  When they balance the draft is accepted — and exported
to ``out`` when given (in the product the program exports after the agent).  When they do not, the answer names
what stands in the way; the agent fixes it and submits again.  Open issues are counted, not blocking: an issue left
open is reported as open.
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


class SubmitDraftRequest(IRModel):
    out: str | None = None  # export to this directory (<name>.md, <name>.blocks.json, images/)
    name: str | None = None  # default: the document id


class SubmitDraftResult(IRModel):
    accepted: bool
    status: DocumentStatus  # complete / partial / failed
    blockers: list[str]  # what keeps the draft from being accepted
    open_issues: dict[str, int]  # issues still open, by kind (reported, not blocking)
    markdown: str | None = None  # exported files, when out was given
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


def run(ctx: ToolContext, req: SubmitDraftRequest) -> ToolOutput[SubmitDraftResult]:
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
    accepted = result.exportable and not blockers
    submitted = SubmitDraftResult(accepted=accepted, status=state.status, blockers=blockers, open_issues=open_issues)
    if accepted and req.out:
        paths = write_export(state, ctx.ws.root, Path(req.out), req.name or state.id)
        submitted.markdown, submitted.sidecar = str(paths.markdown.resolve()), str(paths.sidecar.resolve())
        submitted.summary = str(paths.summary.resolve())
    return output(submitted)
