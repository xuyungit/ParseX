"""Our own agent loop (Q62, Q86): the model calls the four tools as functions, in this process.

The agent has the tools and nothing else — no shell, no files — so what it may touch holds by construction.  The
function definitions are the tools' request models (``tool_schema``), the calls run in-process (``call_tool``, each
logged in the workspace's call records like a command-line call), and each envelope goes back as the agent reads it
(``agent_json``).  The task is Codex's with the function-call adapter (``compose_task("call", …)``).

The model is reached through the Responses API without storing anything on the provider's side: each turn sends the
whole conversation, the reasoning items travelling back encrypted.  The loop ends when the model answers without
calling a tool; it fails at the deadline, after ``max_steps`` model turns, or when the model cannot be reached.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from parserx.config.schema import ParserXConfig
from parserx.runtimes.agent import AgentOutcome, list_price
from parserx.runtimes.codex import AgentUsage
from parserx.tools import AGENT_TOOLS, ToolContext, agent_json, call_tool, tool_schema
from parserx.workspace import Workspace

MAX_STEPS = 200  # model turns; Codex's runs took at most ~50 tool calls a document
RETRIES = 3  # a model turn that fails to arrive is tried again this many times


class LoopAgent:
    engine, adapter = "loop", "call"

    def __init__(self, model: str, effort: str, *, config: ParserXConfig, price=None,
                 context_class: type[ToolContext] = ToolContext, client_factory: Callable[..., Any] | None = None,
                 max_steps: int = MAX_STEPS):
        self.model, self.effort, self.config, self.price = model, effort, config, price
        self.context_class, self.max_steps = context_class, max_steps
        self.client_factory = client_factory or _openai_client

    def available(self) -> tuple[bool, str | None]:
        service = self.config.services.vlm  # the agent model is reached where the service models are
        reachable = service.endpoint or service.api_key or self.client_factory is not _openai_client
        return (True, None) if reachable else (False, "loop_not_configured")

    def run(self, work_dir: Path, deadline_s: float, log_dir: Path,
            on_event: Callable[[dict], None] | None = None) -> AgentOutcome:
        from parserx.runtimes.experiment import compose_task

        ws_dir = Path(work_dir) / "ws"
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        source = Path(Workspace.open(ws_dir).load().source).name
        task = compose_task("call", input_name=source, minutes=round(deadline_s / 60), vision="tool")
        tools = [{"type": "function", "name": name, "description": schema["description"],
                  "parameters": schema["request"]} for name in AGENT_TOOLS for schema in [tool_schema(name)]]
        items: list[dict] = [{"role": "developer", "content": task},
                             {"role": "user", "content": f"开始处理 {source}。"}]
        usage = AgentUsage()
        started = time.monotonic()
        last_message: str | None = None
        reason = detail = None
        with open(log_dir / "trace.jsonl", "w", encoding="utf-8") as trace:
            for step in range(self.max_steps):
                left = deadline_s - (time.monotonic() - started)
                if left <= 0:
                    reason, detail = "agent_timeout", f"{deadline_s / 60:.0f} min"
                    break
                t0 = time.monotonic()
                try:
                    response = self._turn(items, tools, timeout=left)
                except Exception as exc:  # noqa: BLE001 - the model could not be reached: the run fails, reported
                    usage.failed_turns += 1
                    usage.errors.append(f"{type(exc).__name__}: {exc}"[:300])
                    reason, detail = "agent_failed", usage.errors[-1]
                    break
                _count(usage, response)
                output = [item.model_dump(exclude_none=True) for item in response.output]
                items += output
                calls = [item for item in response.output if item.type == "function_call"]
                record = {"step": step, "model_s": round(time.monotonic() - t0, 1),
                          "usage": response.usage.model_dump() if response.usage else None,
                          "output": [o["type"] for o in output], "calls": []}
                if not calls:
                    last_message = response.output_text
                    trace.write(json.dumps(record, ensure_ascii=False) + "\n")
                    break
                for call in calls:
                    t1 = time.monotonic()
                    result = self._call(ws_dir, call.name, call.arguments)
                    items.append({"type": "function_call_output", "call_id": call.call_id, "output": result})
                    record["calls"].append({"name": call.name, "arguments": call.arguments,
                                            "s": round(time.monotonic() - t1, 1), "result_chars": len(result)})
                usage.commands += len(calls)
                trace.write(json.dumps(record, ensure_ascii=False) + "\n")
                trace.flush()
            else:
                reason, detail = "agent_failed", f"no answer after {self.max_steps} model turns"
        if last_message is not None:
            (log_dir / "last_message.md").write_text(last_message, encoding="utf-8")
        outcome = dict(wall_s=round(time.monotonic() - started, 1), usage=usage,
                       usd_at_list_price=list_price(self.price, usage), last_message=last_message)
        if reason is not None:
            return AgentOutcome(ok=False, reason=reason, detail=detail, **outcome)
        return AgentOutcome(ok=True, **outcome)

    def _turn(self, items: list[dict], tools: list[dict], *, timeout: float):
        client = self.client_factory(self.config, timeout=timeout)
        for attempt in range(RETRIES + 1):
            try:
                return client.responses.create(model=self.model, input=items, tools=tools, store=False,
                                               reasoning={"effort": self.effort},
                                               include=["reasoning.encrypted_content"])
            except Exception as exc:  # noqa: BLE001 - a transient failure is tried again, the last one raised
                if attempt == RETRIES or not _transient(exc):
                    raise
                time.sleep(2 ** attempt)

    def _call(self, ws_dir: Path, name: str, arguments: str) -> str:
        """One tool call; what goes back is the envelope as the agent reads it, or why the call was not made."""
        if name not in AGENT_TOOLS:
            return json.dumps({"ok": False, "error": f"no tool {name}; the tools are {', '.join(AGENT_TOOLS)}"},
                              ensure_ascii=False)
        try:
            request = json.loads(arguments or "{}")
        except ValueError as exc:
            return json.dumps({"ok": False, "error": f"the arguments are not JSON: {exc}"}, ensure_ascii=False)
        envelope, _ = call_tool(name, ws_dir, request, config=self.config, context_factory=self.context_class)
        return agent_json(envelope)  # an invalid request comes back as a failure (invalid_request)


def _openai_client(config: ParserXConfig, *, timeout: float):
    from openai import OpenAI

    service = config.services.vlm
    return OpenAI(api_key=service.api_key or "no-key", base_url=service.endpoint or None, max_retries=0,
                  timeout=max(timeout, 1.0),
                  default_headers={"User-Agent": service.user_agent} if service.user_agent else None)


def _transient(exc: Exception) -> bool:
    import openai

    return isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                            openai.InternalServerError))


def _count(usage: AgentUsage, response) -> None:
    usage.turns += 1
    if response.usage is None:
        return
    usage.input_tokens += response.usage.input_tokens
    usage.output_tokens += response.usage.output_tokens
    details = response.usage.input_tokens_details
    usage.cached_input_tokens += getattr(details, "cached_tokens", 0) or 0
    reasoning = getattr(response.usage.output_tokens_details, "reasoning_tokens", 0) or 0
    usage.reasoning_output_tokens += reasoning
    for item in response.output:
        usage.items[item.type] = usage.items.get(item.type, 0) + 1
