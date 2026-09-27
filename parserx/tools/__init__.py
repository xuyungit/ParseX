"""The document toolkit (guide §5): seven tools over the workspace, one envelope, a JSON CLI.

``call_tool(name, ws_dir, request, config=…)`` runs a tool in-process (the
fixed-sequence runtime does this); ``parserx tool <name> …`` is the same call
from a shell.  ``tool_schema(name)`` gives the request and envelope JSON
Schemas for runtime adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from parserx.accounting import CheckResult
from parserx.config.schema import ParserXConfig
from parserx.tools import (
    ask_image,
    check_export,
    close,
    correct,
    describe_figure,
    draft,
    edit,
    overview,
    process,
    read,
    recognize,
    review_table,
    skim,
    source,
    structure,
    submit,
)
from parserx.tools.context import ToolContext, invoke
from parserx.tools.envelope import Envelope
from parserx.tools.init import InitResult, workspace_init


@dataclass(frozen=True)
class ToolSpec:
    request: type[BaseModel]
    result: type[BaseModel]
    run: Callable


AGENT_TOOLS = ("read_draft", "view_source", "edit_draft", "submit_draft")  # what the agent is given (Q85)

TOOLS: dict[str, ToolSpec] = {
    "read_draft": ToolSpec(draft.ReadDraftRequest, draft.ReadDraftResult, draft.run),
    "view_source": ToolSpec(source.ViewSourceRequest, source.ViewSourceResult, source.run),
    "edit_draft": ToolSpec(edit.EditDraftRequest, edit.EditDraftResult, edit.run),
    "submit_draft": ToolSpec(submit.SubmitDraftRequest, submit.SubmitDraftResult, submit.run),
    "run_pipeline": ToolSpec(process.ProcessRequest, process.ProcessResult, process.run),
    "process": ToolSpec(process.ProcessRequest, process.ProcessResult, process.run),
    "overview": ToolSpec(overview.OverviewRequest, overview.OverviewResult, overview.run),
    "read": ToolSpec(read.ReadRequest, read.ReadResult, read.run),
    "skim": ToolSpec(skim.SkimRequest, skim.SkimResult, skim.run),
    "recognize": ToolSpec(recognize.RecognizeRequest, recognize.RecognizeResult, recognize.run),
    "review_table": ToolSpec(review_table.ReviewTableRequest, review_table.ReviewTableResult, review_table.run),
    "correct": ToolSpec(correct.CorrectRequest, correct.CorrectResult, correct.run),
    "close": ToolSpec(close.CloseRequest, close.CloseResult, close.run),
    "ask_image": ToolSpec(ask_image.AskImageRequest, ask_image.AskImageResult, ask_image.run),
    "describe_figure": ToolSpec(describe_figure.DescribeFigureRequest, describe_figure.DescribeFigureResult,
                                describe_figure.run),
    "apply_structure": ToolSpec(structure.ApplyStructureRequest, structure.ApplyStructureResult, structure.run),
    "check": ToolSpec(check_export.CheckRequest, CheckResult, check_export.run_check),
    "export": ToolSpec(check_export.ExportRequest, check_export.ExportResult, check_export.run_export),
}


def call_tool(name: str, ws_dir: Path | str, request: dict | BaseModel | None = None, *, config: ParserXConfig,
              expect_version: int | None = None, context_factory=ToolContext) -> tuple[Envelope, int]:
    spec = TOOLS[name]
    return invoke(name, spec.run, spec.request, ws_dir, request or {}, config=config,
                  expect_version=expect_version, context_factory=context_factory)


def tool_schema(name: str) -> dict[str, Any]:
    if name == "workspace_init":
        return {"request": {"type": "object", "properties": {"input": {"type": "string"}, "ws": {"type": "string"}}},
                "envelope": Envelope[InitResult].model_json_schema(mode="serialization")}
    spec = TOOLS[name]
    return {"request": spec.request.model_json_schema(by_alias=True),
            "envelope": Envelope[spec.result].model_json_schema(mode="serialization")}


__all__ = ["AGENT_TOOLS", "TOOLS", "ToolContext", "call_tool", "tool_schema", "workspace_init"]
