"""Running a tool: context (services, gateway, budget) and the envelope wrapper.

Each tool call is a separate unit of work — often a separate process when the
JSON CLI is used — so the document budget and statistics live in the
workspace (``state.stats``): the context preloads them, and the wrapper adds
the call's requests, tokens, cost and time back after the call.  Every call is
appended to ``calls.jsonl`` with its request and envelope (guide §7.5).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from parserx.cache import CacheMiss, open_cache, service_identity
from parserx.config.schema import ParserXConfig
from parserx.ir.state import TokenUsage
from parserx.scheduling import (
    BudgetExhausted,
    JobStore,
    MeteredService,
    MeterSnapshot,
    RequestMeter,
    ServiceGateway,
    UnparseableResponse,
    is_retryable,
)
from parserx.runtimes.events import Waiting
from parserx.services.llm import create_vlm_service
from parserx.services.ocr import PaddleOCRService
from parserx.tools.envelope import BudgetLeft, Cost, Envelope, Failure, FailureCode, ToolFailure
from parserx.workspace import VersionConflict, Workspace, WorkspaceLocked, WorkspaceTampered

log = logging.getLogger(__name__)
R = TypeVar("R")
_KEEP = object()


@dataclass
class ToolOutput(Generic[R]):
    result: R
    failures: list[Failure]


def output(result: R, *, failures=None) -> ToolOutput[R]:
    return ToolOutput(result, list(failures or []))


class ToolContext:
    def __init__(self, ws: Workspace, config: ParserXConfig):
        self.ws = ws
        self.config = config
        self.meter = RequestMeter()
        self.cache = open_cache(config.cache)
        self.gateway = ServiceGateway.from_config(self.meter, self.cache, config.scheduling)
        stats = ws.load().stats
        self.gateway.budget.preload(stats.requests, stats.cost_usd or 0.0, stats.wall_time_s)
        self._ocr: PaddleOCRService | None = None
        self._vlm: dict[Any, MeteredService] = {}
        self.reporter: Callable[[Any], None] | None = None  # progress events of ``parserx parse`` (P4-2)

    def report(self, event: Any) -> None:
        if self.reporter is not None:
            self.reporter(event)

    # Service factories: tests replace these to plug in fake services behind the real gateway.
    def _new_ocr(self) -> PaddleOCRService:
        cfg = self.config.builders.ocr
        if cfg.engine == "none" or not cfg.endpoint or not cfg.token:
            raise ToolFailure(FailureCode.SERVICE_ERROR, "scan engine not configured (builders.ocr)")
        return PaddleOCRService(cfg)

    def _new_vlm(self, cfg):
        if not cfg.endpoint or not cfg.api_key:
            raise ToolFailure(FailureCode.SERVICE_ERROR, "VLM service not configured (services.vlm)")
        return create_vlm_service(cfg)

    def _new_detector(self):
        from parserx.layout.detector import RapidLayoutDetector

        return RapidLayoutDetector(self.config.layout.model, self.config.layout.conf_thresh, layout=self.config.layout)

    def detector(self):
        if getattr(self, "_detector", None) is None:
            self._detector = self._new_detector()
        return self._detector

    def _new_reader(self):
        from parserx.reading.local import LocalReader

        return LocalReader()

    def reader(self):
        """The local page reader (guide §9.5, Q56)."""
        if getattr(self, "_reader", None) is None:
            self._reader = self._new_reader()
        return self._reader

    def ocr(self) -> PaddleOCRService:
        if self._ocr is None:
            service = self._new_ocr()
            service.gateway = self.gateway
            service.on_wait = lambda state, seconds: self.report(Waiting("ocr", round(seconds, 1), state))
            if self.cache is not None and self.cache.writable:
                service.job_store = JobStore(Path(self.cache.root) / "jobs" / "ocr")
            self._ocr = service
        return self._ocr

    def vlm(self, reasoning_effort: Any = _KEEP) -> MeteredService:
        cfg = self.config.services.vlm
        if reasoning_effort is not _KEEP and reasoning_effort is not None:
            cfg = cfg.model_copy(update={"reasoning_effort": reasoning_effort})
        key = cfg.reasoning_effort
        if key not in self._vlm:
            self._vlm[key] = MeteredService(self._new_vlm(cfg), self.meter, "vlm",
                                            identity=service_identity(cfg), gateway=self.gateway)
        return self._vlm[key]

    def cost(self, wall_s: float, since: MeterSnapshot | None = None) -> Cost:
        snap = _delta(self.meter.snapshot(), since)
        left = self.gateway.budget.left()
        return Cost(
            requests=dict(sorted(snap.requests.items())), attempts=dict(sorted(snap.attempts.items())),
            cache_hits=dict(sorted(snap.cache_hits.items())),
            tokens_in=sum(t["input"] for t in snap.tokens.values()),
            tokens_out=sum(t["output"] for t in snap.tokens.values()),
            usd=snap.cost_usd if snap.tokens else (0.0 if not snap.requests.get("vlm") else None),
            wall_s=round(wall_s, 3),
            budget_left=BudgetLeft(requests=left["requests"], usd=left["usd"], seconds=left["seconds"]),
        )


def service_failure(exc: Exception, targets: list[str]) -> Failure:
    """A per-target failure from a service call."""
    if isinstance(exc, ToolFailure):
        return exc.failure.model_copy(update={"targets": targets})
    if isinstance(exc, BudgetExhausted):
        return Failure(code=FailureCode.BUDGET_EXHAUSTED, message=str(exc), retryable=False, targets=targets)
    if isinstance(exc, CacheMiss):
        return Failure(code=FailureCode.CACHE_MISS_OFFLINE, message=str(exc), retryable=False, targets=targets)
    if isinstance(exc, UnparseableResponse):
        return Failure(code=FailureCode.SERVICE_ERROR, retryable=False, targets=targets, message=(
            f"the model's answer could not be parsed, also when asked a second time ({exc}); the same request "
            "replays the same answers from the cache — change the question (fewer cells, another note) to ask again"))
    if "flagged as potentially violating" in str(exc):
        return Failure(code=FailureCode.SERVICE_ERROR, retryable=False, targets=targets, message=(
            "the VLM endpoint's content policy refused this request; rephrase the issue note and try again, "
            f"or leave the item unresolved ({type(exc).__name__}: {exc})"))
    if isinstance(exc, TimeoutError) or "Timeout" in type(exc).__name__:
        return Failure(code=FailureCode.TIMEOUT, message=f"{type(exc).__name__}: {exc}", retryable=True,
                       targets=targets)
    return Failure(code=FailureCode.SERVICE_ERROR, message=f"{type(exc).__name__}: {exc}",
                   retryable=is_retryable(exc), targets=targets)


def invoke(
    name: str,
    run: Callable[[ToolContext, Any], ToolOutput],
    request_model: type[BaseModel],
    ws_dir: Path | str,
    request: dict | BaseModel,
    *,
    config: ParserXConfig,
    expect_version: int | None = None,
    context_factory: Callable[[Workspace, ParserXConfig], ToolContext] = ToolContext,
) -> tuple[Envelope, int]:
    """Run one tool; returns (envelope, exit code 0 / 1 / 2)."""
    started = time.monotonic()
    try:
        req = request if isinstance(request, request_model) else request_model.model_validate(request)
    except ValidationError as exc:
        return _fatal(name, "", 0, Failure(code=FailureCode.INVALID_REQUEST, message=_short(exc),
                                           retryable=False)), 2
    try:
        ws = Workspace.open(ws_dir)
    except FileNotFoundError as exc:
        return _fatal(name, "", 0, Failure(code=FailureCode.NOT_FOUND, message=str(exc), retryable=False)), 0
    try:
        problem = ws.tampered()
    except WorkspaceLocked as exc:
        return _fatal(name, "", 0, Failure(code=FailureCode.TIMEOUT, message=str(exc), retryable=True)), 0
    if problem is not None:
        envelope = _fatal(name, "", 0, _tampered(problem))
        ws.log_call({"tool": name, "request": req.model_dump(mode="json", by_alias=True),
                     "envelope": envelope.model_dump(mode="json", exclude={"result"}), "result": None})
        return envelope, 0
    state = ws.load()
    if expect_version is not None and state.version != expect_version:
        failure = Failure(code=FailureCode.VERSION_CONFLICT, retryable=True,
                          message=f"workspace is at version {state.version}, request expected {expect_version}")
        return _fatal(name, state.id, state.version, failure), 0
    ctx = context_factory(ws, config)
    before = ctx.meter.snapshot()  # a context may be shared by the calls of one run: count this call only
    code = 0
    try:
        out = run(ctx, req)
        failures, fatal = out.failures, False
    except (KeyboardInterrupt, SystemExit):
        # Stopped from outside (Ctrl-C, a deadline's SIGTERM): the call record still claims what this call
        # committed, so the workspace stays verifiable and a later run can continue from it (P4-1).
        failure = Failure(code=FailureCode.INTERRUPTED, message="the call was interrupted", retryable=True)
        ctx.ws.log_call({"tool": name, "request": req.model_dump(mode="json", by_alias=True),
                         "envelope": _fatal(name, state.id, state.version, failure).model_dump(
                             mode="json", exclude={"result"}), "result": None})
        raise
    except ToolFailure as exc:
        out, failures, fatal = None, [exc.failure], True
        code = 2 if exc.failure.code == FailureCode.INVALID_REQUEST else 0
    except VersionConflict as exc:
        out, fatal = None, True
        failures = [Failure(code=FailureCode.VERSION_CONFLICT, message=str(exc), retryable=True)]
    except WorkspaceLocked as exc:
        out, fatal = None, True
        failures = [Failure(code=FailureCode.TIMEOUT, message=str(exc), retryable=True)]
    except WorkspaceTampered as exc:  # changed by something else while this call ran
        out, fatal = None, True
        failures = [_tampered(str(exc))]
    except Exception as exc:  # noqa: BLE001 - a defect: reported, never swallowed
        log.exception("tool %s failed", name)
        out, fatal, code = None, True, 1
        failures = [Failure(code=FailureCode.INTERNAL_ERROR, message=f"{type(exc).__name__}: {exc}",
                            retryable=False)]
    wall = time.monotonic() - started
    _record_stats(ctx, name, wall, before)
    envelope = Envelope(
        tool=name, doc=state.id, ws_version=ws.load().version, ok=not fatal,
        result=out.result if out is not None else None, cost=ctx.cost(wall, before), failures=failures,
    )
    # The context's workspace committed this call's transactions (a runtime may share one context across calls).
    ctx.ws.log_call({"tool": name, "request": req.model_dump(mode="json", by_alias=True),
                     "envelope": envelope.model_dump(mode="json", exclude={"result"}),
                     "result": envelope.model_dump(mode="json")["result"]})
    return envelope, code


def _delta(after: MeterSnapshot, before: MeterSnapshot | None) -> MeterSnapshot:
    """What happened between two snapshots of the same meter."""
    if before is None:
        return after

    def minus(a: dict, b: dict) -> dict:
        return {k: v - b.get(k, 0) for k, v in a.items() if v - b.get(k, 0)}

    tokens = {}
    for service, t in after.tokens.items():
        old = before.tokens.get(service, {})
        diff = {k: t[k] - old.get(k, 0) for k in t}
        if any(diff.values()):
            tokens[service] = diff
    if after.cost_usd is None or before.cost_usd is None:
        cost = None if after.cost_usd is None and tokens else (after.cost_usd or 0.0) - (before.cost_usd or 0.0)
    else:
        cost = round(after.cost_usd - before.cost_usd, 8)
    return MeterSnapshot(requests=minus(after.requests, before.requests), attempts=minus(after.attempts, before.attempts),
                         pages=minus(after.pages, before.pages), cache_hits=minus(after.cache_hits, before.cache_hits),
                         cache_misses=minus(after.cache_misses, before.cache_misses),
                         skipped_budget=minus(after.skipped_budget, before.skipped_budget), tokens=tokens,
                         cost_usd=cost)


def _record_stats(ctx: ToolContext, name: str, wall: float, before: MeterSnapshot | None = None) -> None:
    snap = _delta(ctx.meter.snapshot(), before)
    if not (snap.requests or snap.cache_hits or snap.cache_misses or snap.skipped_budget):
        return
    with ctx.ws.txn(f"tool:{name}:stats") as state:
        stats = state.stats
        earlier_unpriced = stats.cost_usd is None and any(stats.requests.get(s) for s in ("vlm", "llm"))
        for field in ("requests", "attempts", "cache_hits"):
            merged = dict(getattr(stats, field))
            for service, n in getattr(snap, field).items():
                merged[service] = merged.get(service, 0) + n
            setattr(stats, field, dict(sorted(merged.items())))
        tokens = dict(stats.tokens)
        for service, t in snap.tokens.items():
            old = tokens.get(service, TokenUsage())
            tokens[service] = TokenUsage(input=old.input + t["input"], cached_input=old.cached_input + t["cached_input"],
                                         output=old.output + t["output"])
        stats.tokens = dict(sorted(tokens.items()))
        if snap.cost_usd is None or earlier_unpriced:
            stats.cost_usd = None  # unknown stays unknown
        else:
            stats.cost_usd = round((stats.cost_usd or 0.0) + snap.cost_usd, 8)
        stats.wall_time_s = round(stats.wall_time_s + wall, 3)


def _tampered(problem: str) -> Failure:
    return Failure(code=FailureCode.WORKSPACE_TAMPERED, retryable=False, message=(
        f"{problem}; the tools no longer work on this workspace and it cannot be exported — "
        "change a workspace only through the tools"))


def _fatal(name: str, doc: str, version: int, failure: Failure) -> Envelope:
    return Envelope(tool=name, doc=doc, ws_version=version, ok=False, failures=[failure])


def _short(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'request'}: {e['msg']}" for e in exc.errors()[:5])
