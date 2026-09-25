"""Progress events of ``parserx parse`` (plan P4-1, P4-2): what the runtime tells the console.

The runtime emits events; a reporter renders them (``parserx/console``).  Events carry codes and values, never
display text, so the same run can be shown in either interface language (Q60) or as JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Stage = Literal["read", "process", "agent", "export"]
STAGES: tuple[Stage, ...] = ("read", "process", "agent", "export")


@dataclass(frozen=True)
class DocStart:
    name: str
    source: str
    format: str
    pages: int
    scanned: int  # pages without a usable native text layer
    resumed: bool = False  # continuing an interrupted run from its workspace


@dataclass(frozen=True)
class StageStart:
    stage: Stage
    detail: dict[str, Any] = field(default_factory=dict)  # agent: engine, model


@dataclass(frozen=True)
class StageEnd:
    stage: Stage
    seconds: float
    ok: bool = True
    skipped: str | None = None  # why the stage did not run (a notice code)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step:
    """Progress inside a stage: *step* is a code (recognize, layout, reading, transcribe, describe, structure …)."""

    stage: Stage
    step: str
    done: int | None = None
    total: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Waiting:
    """A service keeps us waiting (e.g. the scan engine's queue)."""

    service: str
    seconds: float
    state: str | None = None  # the service's own word: pending (queued), running


@dataclass(frozen=True)
class ReviewCount:
    open: int
    by_kind: dict[str, int]


@dataclass(frozen=True)
class AgentAction:
    """One thing the agent did, from a tool call record: look, edit, add, set_title, set_level, split, merge,
    exclude, restore, close, recognize, describe, review_table, rejected."""

    action: str
    target: str | None = None  # "p4", a block id
    page: int | None = None
    text: str | None = None  # the document text the action is about (shortened)
    level: int | None = None
    detail: str | None = None  # e.g. the reason of a close


@dataclass(frozen=True)
class Notice:
    """Something the user should know: a code (fallback reasons, warnings) and its values."""

    code: str
    level: Literal["info", "warning", "error"] = "info"
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocEnd:
    outcome: Any  # hybrid.ParseOutcome


Event = DocStart | StageStart | StageEnd | Step | Waiting | ReviewCount | AgentAction | Notice | DocEnd
Reporter = Callable[[Event], None]


def null_reporter(event: Event) -> None:
    return None
