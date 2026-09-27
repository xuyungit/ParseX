"""The agent loop's conversation, kept in our own form, and one adapter per model API (Q88).

The loop keeps the conversation as a list of entries — what the program said (``Note``), what the model answered
(``Reply``: text, tool calls, and the provider's own items such as encrypted reasoning, handed back only to the same
API), and what each tool returned (``ToolResult``: text and images; once cleared, a one-line placeholder).  An
adapter turns the conversation into its API's request and the answer back into a ``Reply``:

- ``ResponsesModel``: the OpenAI Responses API — nothing stored on the provider's side, reasoning returned
  encrypted, images inside the function's output; the provider caches the unchanged prefix by itself;
- ``ChatModel``: OpenAI-compatible Chat Completions (DashScope, DeepSeek, …) — the tools' schemas with their
  references written out, the images of a turn's tool results in a user message right after them.

Whatever the API, the request is the task and the tools (a prefix that never changes) followed by the conversation,
only ever appended to — except when the loop clears old tool results (``runtimes/loop.py``).
"""

from __future__ import annotations

import base64
import copy
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it


@dataclass
class Reply:
    text: str
    calls: list[ToolCall]
    raw: tuple[str, list[dict]] | None = None  # (API, its own items): sent back verbatim to the same API only


@dataclass
class ToolResult:
    call: ToolCall
    text: str
    images: list[Path] = field(default_factory=list)  # images the agent sees itself (vision: agent)
    cleared: str | None = None  # the placeholder that replaced the result


@dataclass
class Note:
    text: str  # the program speaking: the start, a cleared context, time to submit


Entry = Reply | ToolResult | Note


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class Answer:
    reply: Reply
    usage: Usage


class Model(Protocol):
    name: str

    def answer(self, system: str, tools: list[dict], history: list[Entry], *, timeout: float) -> Answer:
        """One model turn on the task (*system*), the tools ({name, description, parameters}) and the history."""


# ── OpenAI Responses API ────────────────────────────────────────────────


class ResponsesModel:
    api = "responses"

    def __init__(self, client, model: str, effort: str | None):
        self.client, self.name, self.effort = client, model, effort

    def answer(self, system: str, tools: list[dict], history: list[Entry], *, timeout: float) -> Answer:
        request: dict[str, Any] = dict(
            model=self.name, input=self.input(system, history), store=False, include=["reasoning.encrypted_content"],
            tools=[{"type": "function", **tool} for tool in tools], timeout=timeout)
        if self.effort:
            request["reasoning"] = {"effort": self.effort}
        response = self.client.responses.create(**request)
        calls = [ToolCall(o.call_id, o.name, o.arguments) for o in response.output if o.type == "function_call"]
        items = [o.model_dump(exclude_none=True) for o in response.output]
        usage = response.usage
        return Answer(Reply(response.output_text or "", calls, (self.api, items)), Usage(
            input_tokens=usage.input_tokens if usage else 0,
            cached_input_tokens=_get(usage, "input_tokens_details", "cached_tokens"),
            output_tokens=usage.output_tokens if usage else 0,
            reasoning_tokens=_get(usage, "output_tokens_details", "reasoning_tokens")))

    def input(self, system: str, history: list[Entry]) -> list[dict]:
        items: list[dict] = [{"role": "developer", "content": system}]
        for entry in history:
            if isinstance(entry, Note):
                items.append({"role": "user", "content": entry.text})
            elif isinstance(entry, Reply):
                if entry.raw is not None and entry.raw[0] == self.api:
                    items += entry.raw[1]
                    continue
                if entry.text:
                    items.append({"role": "assistant", "content": entry.text})
                items += [{"type": "function_call", "call_id": c.id, "name": c.name, "arguments": c.arguments}
                          for c in entry.calls]
            else:
                output: str | list = entry.cleared or entry.text
                if entry.images and entry.cleared is None:
                    output = [{"type": "input_text", "text": entry.text},
                              *({"type": "input_image", "image_url": _data_url(p)} for p in entry.images)]
                items.append({"type": "function_call_output", "call_id": entry.call.id, "output": output})
        return items


# ── OpenAI-compatible Chat Completions ──────────────────────────────────


class ChatModel:
    api = "chat"

    def __init__(self, client, model: str, effort: str | None = None, extra_body: dict | None = None):
        self.client, self.name, self.effort, self.extra_body = client, model, effort, extra_body or {}

    def answer(self, system: str, tools: list[dict], history: list[Entry], *, timeout: float) -> Answer:
        request: dict[str, Any] = dict(
            model=self.name, messages=self.messages(system, history), timeout=timeout,
            tools=[{"type": "function", "function": {**tool, "parameters": inline_refs(tool["parameters"])}}
                   for tool in tools])
        if self.effort:
            request["reasoning_effort"] = self.effort
        if self.extra_body:
            request["extra_body"] = self.extra_body
        response = self.client.chat.completions.create(**request)
        message = response.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in message.tool_calls or []]
        usage = response.usage
        return Answer(Reply(message.content or "", calls), Usage(
            input_tokens=usage.prompt_tokens if usage else 0,
            cached_input_tokens=_get(usage, "prompt_tokens_details", "cached_tokens"),
            output_tokens=usage.completion_tokens if usage else 0,
            reasoning_tokens=_get(usage, "completion_tokens_details", "reasoning_tokens")))

    def messages(self, system: str, history: list[Entry]) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": system}]
        images: list[Path] = []  # the current turn's, sent once its tool results are all in
        for entry in history:
            if not isinstance(entry, ToolResult) and images:
                messages.append(_image_message(images))
                images = []
            if isinstance(entry, Note):
                messages.append({"role": "user", "content": entry.text})
            elif isinstance(entry, Reply):
                message: dict[str, Any] = {"role": "assistant", "content": entry.text or None}
                if entry.calls:
                    message["tool_calls"] = [{"id": c.id, "type": "function",
                                              "function": {"name": c.name, "arguments": c.arguments}}
                                             for c in entry.calls]
                messages.append(message)
            else:
                messages.append({"role": "tool", "tool_call_id": entry.call.id, "content": entry.cleared or entry.text})
                if entry.cleared is None:
                    images += entry.images
        if images:
            messages.append(_image_message(images))
        return messages


def _image_message(images: list[Path]) -> dict:
    return {"role": "user", "content": [{"type": "text", "text": "上面 view_source 调用的图片（按调用顺序）："},
                                        *({"type": "image_url", "image_url": {"url": _data_url(p)}} for p in images)]}


def inline_refs(schema: dict) -> dict:
    """*schema* with every ``$ref`` to its ``$defs`` written out and the pydantic-only ``discriminator`` dropped:
    some APIs take no references in a function's parameters."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
                return {**target, **{k: walk(v) for k, v in node.items() if k != "$ref"}}
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "discriminator")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def _data_url(path: Path) -> str:
    kind = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{kind};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _get(usage, details: str, name: str) -> int:
    return (getattr(getattr(usage, details, None), name, None) or 0) if usage is not None else 0


def summary(entry: ToolResult) -> str:
    """A cleared result's placeholder: which call it was (its arguments shortened)."""
    try:
        args = json.dumps({k: v for k, v in json.loads(entry.call.arguments or "{}").items() if v not in (None, [], {})},
                          ensure_ascii=False)
    except ValueError:
        args = entry.call.arguments
    return f"[已清理：{entry.call.name} {args[:200]} 的结果。状态都在工作区里，需要时再调用一次。]"
