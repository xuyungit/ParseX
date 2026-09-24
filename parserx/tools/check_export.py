"""``check`` and ``export`` (guide §3.3): no export without a balanced ledger."""

from __future__ import annotations

from pathlib import Path

from parserx.accounting import CheckResult, check
from parserx.ir.base import IRModel
from parserx.ir.enums import DocumentStatus
from parserx.ir.state import Missing
from parserx.render import write_export
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import FailureCode, ToolFailure
from parserx.tools.views import unresolved_items


class CheckRequest(IRModel):
    pass


class ExportRequest(IRModel):
    out: str  # directory for <name>.md, <name>.blocks.json and images/
    name: str | None = None  # default: the document id


class ExportResult(IRModel):
    markdown: str
    sidecar: str
    summary: str  # <name>.json, the document summary (Q42)
    status: DocumentStatus
    missing: list[Missing]


def _checked(ctx: ToolContext) -> CheckResult:
    """Run the check and record status and missing items in the workspace when they changed."""
    state = ctx.ws.load()
    result = check(state, ctx.ws.root)
    if state.status != result.document_status or state.missing != result.missing:
        with ctx.ws.txn("tool:check") as state:
            state.status = result.document_status
            state.missing = result.missing
    return result


def run_check(ctx: ToolContext, req: CheckRequest) -> ToolOutput[CheckResult]:
    result = _checked(ctx)
    return output(result, unresolved=unresolved_items(ctx.ws.load()))


def run_export(ctx: ToolContext, req: ExportRequest) -> ToolOutput[ExportResult]:
    result = _checked(ctx)
    if not result.exportable:
        pending = [p.n for p in result.pages if p.status.value == "pending"]
        problems = [f"{len(result.unassigned)} unassigned", f"{len(result.mismatched)} mismatched",
                    f"{len(result.illegal_refs)} illegal references", f"{len(result.missing_assets)} missing assets",
                    f"pending pages {pending}"]
        raise ToolFailure(FailureCode.CHECK_FAILED, "check does not pass: " + ", ".join(problems),
                          targets=result.unassigned + result.mismatched)
    state = ctx.ws.load()
    paths = write_export(state, ctx.ws.root, Path(req.out), req.name or state.id)
    return output(ExportResult(markdown=str(paths.markdown.resolve()), sidecar=str(paths.sidecar.resolve()),
                               summary=str(paths.summary.resolve()), status=state.status, missing=state.missing),
                  unresolved=unresolved_items(state))
