"""The agent's tool reference, made from the request models (Q86): what each tool does, its parameters and, for
``edit_draft``, its operations — the same words the JSON Schema carries, laid out for reading.  The task
(``runtimes/agent_task.md``) includes it; how a runtime passes the parameters (options on a command line, a function
call) is the runtime adapter's part, not this."""

from __future__ import annotations

import typing
from enum import Enum
from typing import Literal

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from parserx.hierarchy.changes import BLOCK, EVIDENCE, EVIDENCE_OPTIONAL, REASON
from parserx.tools.cli import AGENT_TOOLS, _bare, _is_model

_SHARED = {BLOCK, REASON, EVIDENCE, EVIDENCE_OPTIONAL}  # described once, above the operations
_ENUM_SHOWN = 8  # an enum with more values is named by its kind, not listed


def reference() -> str:
    from parserx.tools import TOOLS

    parts = []
    for name in AGENT_TOOLS:
        spec = TOOLS[name]
        parts.append(f"### `{name}`\n\n{spec.description}")
        if name == "edit_draft":
            parts.append(_operations(spec.request))
        elif spec.request.model_fields:
            parts.append(_fields(spec.request))
            for model in _nested(spec.request):
                parts.append(f"`{_name(model)}` 的每一项：\n\n" + _fields(model))
        else:
            parts.append("没有参数。")
    return "\n\n".join(parts) + "\n"


def _fields(model: type[BaseModel]) -> str:
    rows = ["| 参数 | 取值 | 默认 | 说明 |", "|---|---|---|---|"]
    for field_name, field in model.model_fields.items():
        rows.append(f"| `{field.alias or field_name}` | {_type(field.annotation)} | {_default(field)} | "
                    f"{field.description or ''} |")
    return "\n".join(rows)


def _operations(request: type[BaseModel]) -> str:
    ops_field = request.model_fields["ops"]
    union = typing.get_args(_bare(typing.get_args(ops_field.annotation)[0]))
    lines = [f"参数：`ops`（{ops_field.description}）、`atomic`（{request.model_fields['atomic'].description}）。",
             f"每条操作的 `op` 是操作名，其余字段见括号（带 ? 的可省）；`block` 是{BLOCK}，`reason` 是{REASON}，"
             f"`evidence` 是{EVIDENCE}。", ""]
    for op in union:
        name = typing.get_args(op.model_fields["op"].annotation)[0]
        signature, notes = [], []
        for field_name, field in op.model_fields.items():
            if field_name == "op":
                continue
            key = field.alias or field_name
            signature.append(key + ("" if field.is_required() else "?"))
            if field.description not in _SHARED:
                notes.append(f"`{key}`：{field.description or _type(field.annotation)}")
        lines.append(f"- **`{name}`**（{', '.join(signature)}）：{op.model_json_schema().get('description', '')}")
        if notes:
            lines.append("  " + "；".join(notes))
    return "\n".join(lines)


def _nested(model: type[BaseModel]) -> list[type[BaseModel]]:
    """Object types inside *model*'s fields (a list of looks, of table issues), in order, once each."""
    seen: list[type[BaseModel]] = []

    def walk(annotation) -> None:
        annotation = _bare(annotation)
        if _is_model(annotation):
            if annotation not in seen:
                seen.append(annotation)
                for field in annotation.model_fields.values():
                    walk(field.annotation)
        for arg in typing.get_args(annotation):
            walk(arg)

    for field in model.model_fields.values():
        walk(field.annotation)
    return seen


def _name(model: type[BaseModel]) -> str:
    return {"Look": "looks", "TableIssue": "issues", "CellEdit": "cells"}.get(model.__name__, model.__name__)


def _type(annotation, *, short: bool = False) -> str:
    annotation = _bare(annotation)
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is Literal:
        return "" if short and len(args) == 1 else " / ".join(str(a) for a in args)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [e.value for e in annotation]
        return " / ".join(values) if len(values) <= _ENUM_SHOWN else "类别名"
    if origin is list:
        inner = _bare(args[0])
        return f"`{_name(inner)}` 的列表" if _is_model(inner) else f"列表：{_type(inner)}"
    if origin is tuple:
        return f"{len(args)} 个数"
    if _is_model(annotation):
        return f"`{_name(annotation)}`"
    if short:
        return ""
    return {str: "文字", int: "整数", float: "数", bool: "true / false"}.get(annotation, "")


def _default(field: FieldInfo) -> str:
    if field.is_required():
        return "必填"
    default = field.default
    if default is None or default == [] or default == {}:
        return ""
    if isinstance(default, bool):
        return "true" if default else "false"
    return str(default.value if isinstance(default, Enum) else default)
