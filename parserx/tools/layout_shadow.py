"""``recognize --engine layout``: shadow layout detection (plan P1-9)."""

from __future__ import annotations

from parserx.tools.envelope import FailureCode, ToolFailure


def run_layout(ctx, req):
    raise ToolFailure(FailureCode.INVALID_REQUEST, "the layout engine is not installed yet (plan P1-9)")
