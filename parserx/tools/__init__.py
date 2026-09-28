"""The document toolkit (guide §5, Q85, Q86): four tools over the workspace, one envelope, a JSON CLI.

The agent reads the draft (``read_draft``), looks at the source (``view_source``), changes the draft
(``edit_draft``) and hands it in (``submit_draft``).  ``run_pipeline`` makes the first draft and ``export`` writes the
output package — the program runs them, before and after the agent; they are not the agent's tools.  The pipeline's
own steps (``recognize`` pages with the scan engine, ``describe_figure``) are callable in-process too.

The request models are the contract (Q86): their descriptions are what an agent reads, and the command line
(``tools/cli.py``), the task's tool reference (``tools/reference.py``) and a function-calling runtime's tool
definitions are all made from them.  ``call_tool(name, ws_dir, request, config=…)`` runs one in-process;
``parserx dev tool <name> …`` is the same call from a shell; ``tool_schema(name)`` gives the JSON Schemas.
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
    description: str = ""  # what the agent reads about the tool (the agent's tools only)


TOOLS: dict[str, ToolSpec] = {
    "read_draft": ToolSpec(draft.ReadDraftRequest, draft.ReadDraftResult, draft.run, draft.DESCRIPTION),
    "view_source": ToolSpec(source.ViewSourceRequest, source.ViewSourceResult, source.run, source.DESCRIPTION),
    "edit_draft": ToolSpec(edit.EditDraftRequest, edit.EditDraftResult, edit.run, edit.DESCRIPTION),
    "submit_draft": ToolSpec(submit.SubmitDraftRequest, submit.SubmitDraftResult, submit.run, submit.DESCRIPTION),
    "run_pipeline": ToolSpec(process.ProcessRequest, process.ProcessResult, process.run),
}

# The program's steps, run in-process (not on the command line, not the agent's).
STEPS: dict[str, ToolSpec] = {
    "export": ToolSpec(submit.ExportRequest, submit.ExportResult, submit.export),
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
    return {"description": spec.description, "request": spec.request.model_json_schema(by_alias=True),
            "envelope": _absent_when_null(Envelope[spec.result].model_json_schema(mode="serialization"))}


def agent_json(envelope: Envelope) -> str:
    """An envelope as the agent reads it: fields without a value (null) are left out."""
    return envelope.model_dump_json(by_alias=True, exclude_none=True)


def _absent_when_null(schema: Any) -> Any:
    """The envelope schema as ``agent_json`` writes it: a field that may be null may be absent (absent means null)."""
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict) and "required" in schema:
            schema["required"] = [k for k in schema["required"] if not _nullable(properties.get(k, {}))]
        for value in schema.values():
            _absent_when_null(value)
    elif isinstance(schema, list):
        for value in schema:
            _absent_when_null(value)
    return schema


def _nullable(prop: dict) -> bool:
    return prop.get("type") == "null" or any(o.get("type") == "null" for o in prop.get("anyOf", []))


__all__ = ["AGENT_TOOLS", "STEPS", "TOOLS", "ToolContext", "agent_json", "call_tool", "tool_schema", "workspace_init"]
