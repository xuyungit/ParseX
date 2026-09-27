"""The document toolkit (guide §5, Q85): four tools over the workspace, one envelope, a JSON CLI.

The agent reads the draft (``read_draft``), looks at the source (``view_source``), changes the draft
(``edit_draft``) and hands it in (``submit_draft``).  ``run_pipeline`` makes the first draft — the program runs it
before the agent starts; it is not the agent's tool.  The pipeline's own steps (``recognize`` pages with the scan
engine, ``describe_figure``) are callable in-process for the pipeline and its tests.

``call_tool(name, ws_dir, request, config=…)`` runs one in-process; ``parserx tool <name> …`` is the same call from
a shell.  ``tool_schema(name)`` gives the request and envelope JSON Schemas for runtime adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from parserx.config.schema import ParserXConfig
from parserx.tools import describe_figure, draft, edit, process, recognize, source, submit
from parserx.tools.cli import AGENT_TOOLS
from parserx.tools.context import ToolContext, invoke
from parserx.tools.envelope import Envelope
from parserx.tools.init import InitResult, workspace_init


@dataclass(frozen=True)
class ToolSpec:
    request: type[BaseModel]
    result: type[BaseModel]
    run: Callable


TOOLS: dict[str, ToolSpec] = {
    "read_draft": ToolSpec(draft.ReadDraftRequest, draft.ReadDraftResult, draft.run),
    "view_source": ToolSpec(source.ViewSourceRequest, source.ViewSourceResult, source.run),
    "edit_draft": ToolSpec(edit.EditDraftRequest, edit.EditDraftResult, edit.run),
    "submit_draft": ToolSpec(submit.SubmitDraftRequest, submit.SubmitDraftResult, submit.run),
    "run_pipeline": ToolSpec(process.ProcessRequest, process.ProcessResult, process.run),
}

# Steps of the pipeline, run in-process (not on the command line, not the agent's).
STEPS: dict[str, ToolSpec] = {
    "recognize": ToolSpec(recognize.RecognizeRequest, recognize.RecognizeResult, recognize.run),
    "describe_figure": ToolSpec(describe_figure.DescribeFigureRequest, describe_figure.DescribeFigureResult,
                                describe_figure.run),
}


def call_tool(name: str, ws_dir: Path | str, request: dict | BaseModel | None = None, *, config: ParserXConfig,
              expect_version: int | None = None, context_factory=ToolContext) -> tuple[Envelope, int]:
    spec = TOOLS.get(name) or STEPS[name]
    return invoke(name, spec.run, spec.request, ws_dir, request or {}, config=config,
                  expect_version=expect_version, context_factory=context_factory)


def tool_schema(name: str) -> dict[str, Any]:
    if name == "workspace_init":
        return {"request": {"type": "object", "properties": {"input": {"type": "string"}, "ws": {"type": "string"}}},
                "envelope": Envelope[InitResult].model_json_schema(mode="serialization")}
    spec = TOOLS.get(name) or STEPS[name]
    return {"request": spec.request.model_json_schema(by_alias=True),
            "envelope": Envelope[spec.result].model_json_schema(mode="serialization")}


__all__ = ["AGENT_TOOLS", "STEPS", "TOOLS", "ToolContext", "call_tool", "tool_schema", "workspace_init"]
