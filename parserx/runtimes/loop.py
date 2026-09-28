"""Our own agent loop (Q62, Q86, Q88): the model calls the four tools as functions, in this process.

The agent has the tools and nothing else — no shell, no files — so what it may touch holds by construction.  The
function definitions are the tools' request models (``tool_schema``), the calls run in-process (``call_tool``, each
logged in the workspace's call records like a command-line call), and each envelope goes back as the agent reads it
(``agent_json``).  The task is Codex's with the function-call adapter (``compose_task("call", …)``).  The model is
reached through an adapter (``runtimes/models.py``): the OpenAI Responses API or OpenAI-compatible Chat Completions.

The context (guide Q88): the task and the tools are a prefix that never changes, the conversation is only appended
to, so the provider's cache carries every turn's history.  When the last turn's context passed ``clear_at_tokens``,
or holds more than ``MAX_IMAGES`` images, the older tool results are replaced at once by one-line placeholders (the
state is in the workspace: anything can be read again), and the agent is told so, with its notes (Q87).  A clearing
makes the cache start over from where it cut, so it is rare and thorough: all but the latest results go, images down
to half the limit, and the next clearing waits until the context has grown by half the threshold again (s7: clearing a
result or two every turn doubled the cost of an 87-page document).  With ``vision: agent`` the images
of ``view_source``'s ``as: image`` looks go into the tool results; with ``vision: tool`` the agent asks the service
VLM instead (``as: answer``).

The loop ends when the model answers without calling a tool.  At 80% of the deadline, or when the agent's cost
reaches its budget, it is told to submit; it fails at the deadline, at 1.2 times the budget, after ``max_steps``
model turns, or when the model cannot be reached.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from parserx.config.schema import ParserXConfig, effort_for
from parserx.runtimes.agent import AgentOutcome, list_price
from parserx.runtimes.codex import AgentUsage
from parserx.runtimes.models import Answer, ChatModel, Model, Note, ResponsesModel, ToolCall, ToolResult, summary
from parserx.tools import AGENT_TOOLS, ToolContext, agent_json, call_tool, tool_schema
from parserx.workspace import Workspace
from parserx.workspace.queries import current_notes
from parserx.workspace.store import read_records

MAX_STEPS = 200  # model turns; Codex's runs took at most ~50 tool calls a document
RETRIES = 3  # a model turn that fails to arrive is tried again this many times
KEEP_RESULTS = 4  # tool results a clearing leaves in place, the latest
MAX_IMAGES = 6  # images in the context before a clearing
WRAP_UP = 0.8  # of the deadline: time to submit
OVER_BUDGET = 1.2  # of the budget: stopped

START = "开始处理 {source}。"
SUBMIT_NOW = "{why}：现在调用 submit_draft 交稿，然后写最终报告；不要再开始新的检查。未处理完的待办写进报告。"
CLEARED = ("上下文已清理：较早的 {n} 个工具结果换成了占位，需要时再调用一次（状态都在工作区里）。"
           "已被接受的修改 {changes} 条（read_draft 的 changes 视图）。{notes}")


class LoopAgent:
    engine, adapter = "loop", "call"

    def __init__(self, model: str, effort: str, *, config: ParserXConfig, price=None,
                 context_class: type[ToolContext] = ToolContext, model_factory: Callable[[float], Model] | None = None,
                 max_steps: int = MAX_STEPS):
        self.model, self.effort, self.config, self.price = model, effort, config, price
        self.agent = config.runtime.agent
        self.context_class, self.max_steps = context_class, max_steps
        self.model_factory = model_factory or self._model

    def available(self) -> tuple[bool, str | None]:
        if self.model_factory != self._model:
            return True, None
        service = self.config.services.vlm  # the agent model is reached where the service models are, by default
        endpoint, key = (self.agent.endpoint, self.agent.api_key) if self.agent.endpoint else (service.endpoint,
                                                                                               service.api_key)
        if not endpoint:
            return False, "loop_not_configured"
        return (True, None) if key else (False, "loop_no_key")

    def run(self, work_dir: Path, deadline_s: float, log_dir: Path,
            on_event: Callable[[dict], None] | None = None) -> AgentOutcome:
        from parserx.runtimes.experiment import compose_task

        ws_dir = Path(work_dir) / "ws"
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        source = Path(Workspace.open(ws_dir).load().source).name
        vision = self.agent.vision
        system = compose_task("call", input_name=source, minutes=round(deadline_s / 60), vision=vision)
        tools = [{"name": name, "description": schema["description"], "parameters": schema["request"]}
                 for name in AGENT_TOOLS for schema in [tool_schema(name)]]
        history: list = [Note(START.format(source=source))]
        usage = AgentUsage()
        started = time.monotonic()
        last_message: str | None = None
        reason = detail = None
        context_tokens = 0
        floor = 0  # the context just after the last clearing: the next one waits for it to grow
        after_clearing = False
        told_to_submit = False
        budget = self.agent.budget_usd
        with open(log_dir / "trace.jsonl", "w", encoding="utf-8") as trace:
            for step in range(self.max_steps):
                elapsed = time.monotonic() - started
                cost = list_price(self.price, usage) or 0.0
                if elapsed >= deadline_s:
                    reason, detail = "agent_timeout", f"{deadline_s / 60:.0f} min"
                    break
                if budget is not None and cost >= OVER_BUDGET * budget:
                    reason, detail = "agent_budget", f"${cost:.2f} of ${budget:.2f}"
                    break
                events: list[str] = []
                why = "时间快到了" if elapsed >= WRAP_UP * deadline_s else (
                    "预算快用完了" if budget is not None and cost >= budget else None)
                if why and not told_to_submit:
                    history.append(Note(SUBMIT_NOW.format(why=why)))
                    told_to_submit = True
                    events.append("told to submit")
                clear_at = self.agent.clear_at_tokens
                cleared = _clear(history, context_tokens, max(clear_at, floor + clear_at // 2))
                after_clearing = after_clearing or bool(cleared)
                if cleared:
                    history.append(Note(CLEARED.format(n=cleared, changes=_changes(ws_dir), notes=_notes(ws_dir))))
                    events.append(f"cleared {cleared}")
                t0 = time.monotonic()
                try:
                    answer = self._answer(system, tools, history, timeout=deadline_s - elapsed)
                except Exception as exc:  # noqa: BLE001 - the model could not be reached: the run fails, reported
                    usage.failed_turns += 1
                    usage.errors.append(f"{type(exc).__name__}: {exc}"[:300])
                    reason, detail = "agent_failed", usage.errors[-1]
                    break
                _count(usage, answer)
                context_tokens = answer.usage.input_tokens
                if after_clearing:
                    floor, after_clearing = context_tokens, False
                reply = answer.reply
                history.append(reply)
                record: dict[str, Any] = {"step": step, "model_s": round(time.monotonic() - t0, 1),
                                          "usage": vars(answer.usage), "events": events, "text_chars": len(reply.text),
                                          "calls": []}
                if not reply.calls:
                    last_message = reply.text
                    trace.write(json.dumps(record, ensure_ascii=False) + "\n")
                    break
                for call in reply.calls:
                    t1 = time.monotonic()
                    result = self._call(ws_dir, call, vision)
                    history.append(result)
                    record["calls"].append({"name": call.name, "arguments": call.arguments,
                                            "s": round(time.monotonic() - t1, 1), "result_chars": len(result.text),
                                            "images": len(result.images)})
                usage.commands += len(reply.calls)
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

    def _answer(self, system: str, tools: list[dict], history: list, *, timeout: float) -> Answer:
        model = self.model_factory(timeout)
        for attempt in range(RETRIES + 1):
            try:
                return model.answer(system, tools, history, timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - a transient failure is tried again, the last one raised
                if attempt == RETRIES or not _transient(exc):
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def _model(self, timeout: float) -> Model:
        from openai import OpenAI

        service = self.config.services.vlm
        endpoint = self.agent.endpoint or service.endpoint
        key = self.agent.api_key or (service.api_key if not self.agent.endpoint else "")
        client = OpenAI(api_key=key or "no-key", base_url=endpoint or None, max_retries=0, timeout=max(timeout, 1.0),
                        default_headers={"User-Agent": service.user_agent} if service.user_agent else None)
        effort = effort_for(self.effort, self.agent.efforts, higher=True)  # capability first (Q103)
        if self.agent.api == "chat":
            return ChatModel(client, self.model, effort, extra_body=self.agent.extra_body,
                             cache_markers=self.agent.cache_markers)
        return ResponsesModel(client, self.model, effort)

    def _call(self, ws_dir: Path, call: ToolCall, vision: str) -> ToolResult:
        """One tool call: the envelope as the agent reads it (or why the call was not made), with the images of
        ``as: image`` looks when the agent sees images itself."""
        if call.name not in AGENT_TOOLS:
            return ToolResult(call, _error(f"no tool {call.name}; the tools are {', '.join(AGENT_TOOLS)}"))
        try:
            request = json.loads(call.arguments or "{}")
        except ValueError as exc:
            return ToolResult(call, _error(f"the arguments are not JSON: {exc}"))
        envelope, _ = call_tool(call.name, ws_dir, request, config=self.config, context_factory=self.context_class)
        images = []
        if vision == "agent" and call.name == "view_source" and envelope.result is not None:
            images = [Path(r.image.path) for r in envelope.result.results if r.image is not None]
        return ToolResult(call, agent_json(envelope), images)  # an invalid request comes back as a failure


def _clear(history: list, context_tokens: int, threshold: int) -> int:
    """Replace older tool results by placeholders: all but the latest KEEP_RESULTS when the context reached
    *threshold* tokens; the oldest ones with images until at most half of MAX_IMAGES remain when there are more than
    MAX_IMAGES.  Returns how many were cleared."""
    live = [e for e in history if isinstance(e, ToolResult) and e.cleared is None]
    older = live[:-KEEP_RESULTS] if context_tokens >= threshold else []
    images = sum(len(e.images) for e in live if e not in older)
    if images > MAX_IMAGES:
        for entry in live[:-1]:
            if images <= MAX_IMAGES // 2:
                break
            if entry not in older and entry.images:
                older.append(entry)
                images -= len(entry.images)
    for entry in older:
        entry.cleared = summary(entry)
    return len(older)


def _changes(ws_dir: Path) -> int:
    count = 0
    for record in read_records(Path(ws_dir) / "calls.jsonl"):
        if record.get("type") == "call" and record.get("tool") == "edit_draft" and record.get("result"):
            count += sum(1 for o in record["result"].get("outcomes") or [] if o.get("accepted"))
    return count


def _notes(ws_dir: Path) -> str:
    """The agent's current notes, put back into the context after a clearing (Q87, Q88)."""
    notes = current_notes(Workspace.open(ws_dir).load())
    if not notes:
        return "还没有理解记录（edit_draft 的 note）。"
    return "你记下的理解：\n" + "\n".join(f"- [{n.id}] {n.scope}：{n.text}" for n in notes)


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _transient(exc: Exception) -> bool:
    import openai

    return isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, openai.RateLimitError,
                            openai.InternalServerError))


def _count(usage: AgentUsage, answer: Answer) -> None:
    usage.turns += 1
    usage.input_tokens += answer.usage.input_tokens
    usage.cached_input_tokens += answer.usage.cached_input_tokens
    usage.output_tokens += answer.usage.output_tokens
    usage.reasoning_output_tokens += answer.usage.reasoning_tokens
    usage.items["tool_call"] = usage.items.get("tool_call", 0) + len(answer.reply.calls)
