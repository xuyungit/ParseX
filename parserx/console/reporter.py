"""The console of ``parserx parse`` (plan P4-2): progress, the agent's actions, the result — quiet, in place.

``ConsoleReporter`` renders the runtime's events (``runtimes/events.py``) to a stream (stderr):

- on a terminal, the line of the step in progress is updated in place (and ticks while a stage runs); finished
  steps, the agent's actions and the result stay as ordinary lines;
- elsewhere (pipes, CI) the same lines are written once each, nothing is redrawn;
- ``quiet``: only errors and the result;
- ``compact`` (several documents): one line per document.

Only the standard library: ANSI "clear line" and carriage return, nothing else.
"""

from __future__ import annotations

import shutil
import sys
import threading
import time
import unicodedata
from pathlib import Path
from typing import TextIO

from parserx.console.messages import duration, money, t
from parserx.runtimes.events import (
    STAGES,
    AgentAction,
    DocEnd,
    DocStart,
    Notice,
    ReviewCount,
    StageEnd,
    StageStart,
    Step,
    Waiting,
)

INDENT = "      "
RESULT_COLUMN = 48  # where "✓ 0.8 s" of a finished step starts, in display columns
_CLEAR = "\r\x1b[2K"


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 0 if unicodedata.combining(c) else 1
               for c in text)


def fit(text: str, width: int) -> str:
    """*text* cut to *width* display columns (a live line must not wrap: it could not be cleared)."""
    if display_width(text) <= width:
        return text
    out, used = [], 0
    for c in text:
        w = display_width(c)
        if used + w > width - 1:
            break
        out.append(c)
        used += w
    return "".join(out) + "…"


def pad(text: str, column: int = RESULT_COLUMN) -> str:
    return text + " " * max(1, column - display_width(text))


def shown_path(path: str | Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


class ConsoleReporter:
    def __init__(self, stream: TextIO | None = None, *, lang: str = "zh", tty: bool | None = None,
                 quiet: bool = False, verbose: bool = False, compact: bool = False, tick_s: float = 1.0,
                 clock=time.monotonic):
        self.stream = stream or sys.stderr
        self.lang = lang
        self.tty = self.stream.isatty() if tty is None else tty
        self.quiet, self.verbose, self.compact = quiet, verbose, compact
        self.clock = clock
        self._lock = threading.RLock()
        self._live: str | None = None
        self._live_base: str | None = None  # the live line without its clock
        self._live_since = 0.0
        self._doc: DocStart | None = None
        self._open = 0
        self._step: tuple[str, str, float] | None = None  # (code, text, started)
        self._structure_started: float | None = None  # titles and check are shown as one line
        self._stage_started = 0.0
        self._stage: str | None = None
        self._index = (0, 0)  # compact: document i of n
        self._ticker: threading.Thread | None = None
        self._stop = threading.Event()
        self.tick_s = tick_s
        if self.tty and tick_s > 0:
            self._ticker = threading.Thread(target=self._tick, daemon=True)
            self._ticker.start()

    # ── output primitives ─────────────────────────────────────────────

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def line(self, text: str) -> None:
        """A permanent line (above the live line on a terminal)."""
        with self._lock:
            if self.tty and self._live is not None:
                self._write(_CLEAR)
            self._write(text + "\n")
            if self.tty and self._live is not None:
                self._write(self._live)

    def _set_live(self, base: str | None, *, clock: bool = True) -> None:
        if not self.tty or self.quiet:
            return
        with self._lock:
            if base != self._live_base:
                self._live_since = self.clock()
            self._live_base = base if clock else None
            self._render_live(base, clock)

    def _render_live(self, base: str | None, clock: bool) -> None:
        if base is None:
            if self._live is not None:
                self._write(_CLEAR)
            self._live = None
            return
        text = base
        if clock:
            elapsed = self.clock() - self._live_since
            if elapsed >= 2:
                text = f"{base} … {duration(self.lang, elapsed)}"
        width = shutil.get_terminal_size((100, 20)).columns - 1
        self._live = fit(text, width)
        self._write(_CLEAR + self._live)

    def _tick(self) -> None:
        while not self._stop.wait(self.tick_s):
            with self._lock:
                if self._live_base is not None:
                    self._render_live(self._live_base, True)

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            self._live_base = None
            self._render_live(None, False)

    def _msg(self, key: str, **values) -> str:
        return t(self.lang, key, **values)

    # ── events ────────────────────────────────────────────────────────

    def __call__(self, event) -> None:
        with self._lock:
            handler = getattr(self, f"_on_{type(event).__name__}", None)
            if handler is not None:
                handler(event)

    def start_document(self, index: int, total: int) -> None:
        """Compact mode: document *index* of *total* is next."""
        self._index = (index, total)

    def _stage_label(self, stage: str, detail: dict | None = None) -> str:
        n = STAGES.index(stage) + 1
        if stage == "agent" and detail and detail.get("engine"):
            name = self._msg("stage.agent_with", engine=detail["engine"].capitalize(), model=detail["model"])
        else:
            name = self._msg(f"stage.{stage}")
        return f"[{n}/{len(STAGES)}] {name}"

    def _compact_live(self, text: str) -> None:
        i, n = self._index
        name = self._doc.source if self._doc else ""
        prefix = f"[{i}/{n}] " if n > 1 else ""
        self._set_live(f"{prefix}{name}  {text}")

    def _on_StageStart(self, e: StageStart) -> None:
        self._stage, self._stage_started = e.stage, self.clock()
        label = self._stage_label(e.stage, e.detail)
        if self.compact:
            self._compact_live(self._msg(f"stage.{e.stage}"))
            return
        if e.stage in ("read", "export"):
            self._set_live(label)
        elif not self.quiet:
            self.line(label)
            self._set_live(INDENT + ("…" if e.stage == "process" else ""), clock=True)

    def _on_DocStart(self, e: DocStart) -> None:
        self._doc = e
        if self.compact or self.quiet:
            return
        if e.format == "docx":
            pages = self._msg("header_docx")
        elif e.scanned:
            pages = self._msg("header_pages_scanned", pages=e.pages, scanned=e.scanned)
        else:
            pages = self._msg("header_pages", pages=e.pages)
        self.line(self._msg("header", source=e.source, pages=pages)
                  + (f"  · {self._msg('resumed')}" if e.resumed else ""))

    def _on_Step(self, e: Step) -> None:
        text = self._msg(f"step.{e.step}", done=e.done if e.done is not None else 0, total=e.total or 0)
        if self._step is None or self._step[0] != e.step:
            self._finish_step()
            self._step = (e.step, text, self.clock())
            if e.step == "structure":
                self._structure_started = self.clock()
        else:
            self._step = (e.step, text, self._step[2])
        if self.compact:
            self._compact_live(text)
        else:
            self._set_live(INDENT + text)

    def _finish_step(self) -> None:
        if self._step is None:
            return
        code, text, started = self._step
        self._step = None
        if self.compact or self.quiet or code == "structure":
            return
        if code == "check" and self._structure_started is not None:
            text = f"{self._msg('step.structure')} · {text}"
            started, self._structure_started = self._structure_started, None
        self.line(pad(INDENT + text) + f"✓ {duration(self.lang, self.clock() - started)}")

    def _on_Waiting(self, e: Waiting) -> None:
        if self._step is None or not self.tty:
            return
        key = {"pending": "wait.pending", "running": "wait.running"}.get(e.state or "", "wait.other")
        text = f"{self._step[1]} · {self._msg(key, seconds=duration(self.lang, e.seconds))}"
        if self.compact:
            self._compact_live(text)
        else:
            self._set_live(INDENT + text, clock=False)

    def _on_ReviewCount(self, e: ReviewCount) -> None:
        self._open = e.open
        if self.compact or self.quiet:
            return
        if e.open:
            kinds = " · ".join(f"{self._msg(f'kind.{k}')} {n}" for k, n in
                               sorted(e.by_kind.items(), key=lambda kv: -kv[1]))
            self.line(INDENT + self._msg("review", n=e.open, kinds=kinds))
        else:
            self.line(INDENT + self._msg("review_none"))

    def _on_StageEnd(self, e: StageEnd) -> None:
        self._finish_step()
        if self.compact:
            return
        if e.stage == "agent" and e.skipped in ("no_review_items", "mode_fixed"):
            if not self.quiet:
                self.line(pad(self._stage_label("agent")) + self._msg(f"skip.{e.skipped}"))
            return
        if e.stage == "agent" and not e.ok:
            self._set_live(None)
            self._fallback(e)
            return
        if self.quiet:
            return
        if e.stage in ("read", "export"):
            self._set_live(None)
            self.line(pad(self._stage_label(e.stage)) + f"✓ {duration(self.lang, e.seconds)}")
        elif e.stage == "process":
            self._set_live(None)
        elif e.stage == "agent":
            self._set_live(None)
            d = e.detail
            self.line(INDENT + "✓ " + self._msg("agent_done", seconds=duration(self.lang, e.seconds),
                                                changes=d.get("changes", 0), added=d.get("added", 0),
                                                closed=d.get("closed", 0), open=d.get("open", 0)))

    def _fallback(self, e: StageEnd) -> None:
        reason = e.skipped or e.detail.get("reason") or "agent_failed"
        why = self._msg(f"why.{reason}", detail=e.detail.get("detail") or "",
                        seconds=duration(self.lang, e.seconds))
        name = self._doc.name if self._doc else ""
        open_part = self._msg("fallback_open", n=self._open, file=f"{name}.json") if self._open else ""
        if self.compact:
            return
        self.line(INDENT + "⚠ " + why)
        self.line(INDENT + self._msg("fallback", open=open_part))
        if reason != "workspace_tampered":
            self.line(INDENT + self._msg("fallback_resume"))

    def _on_AgentAction(self, e: AgentAction) -> None:
        if self.quiet:
            return
        where = self._msg("page", page=e.page) if e.page is not None else (e.target or "")
        parts = [self._msg(f"act.{e.action}")]
        if e.action == "set_title" or e.action == "set_level":
            parts.append(" ".join(p for p in (e.text or where, self._msg("level", level=e.level)
                                              if e.level is not None else "") if p))
        else:
            detail = e.text or e.detail
            parts.append(" · ".join(p for p in (where, detail) if p))
        text = "  ".join(p for p in parts if p)
        if self.compact:
            self._compact_live(text)
        else:
            self.line(INDENT + text)
            self._set_live(INDENT + "…")

    def _on_Notice(self, e: Notice) -> None:
        if e.code == "tool_failures":
            for failure in e.args.get("failures", [])[:5]:
                targets = ",".join(failure.get("targets") or []) or "—"
                what = self._msg(f"failure.{failure['code']}") if f"failure.{failure['code']}" in _KNOWN_FAILURES \
                    else self._msg("failure.other")
                if self.verbose:
                    what += f": {failure.get('message')}"
                retry = self._msg("retryable" if failure.get("retryable") else "not_retryable")
                self.line(INDENT + "⚠ " + self._msg("notice.tool_failures", targets=targets, what=what, retry=retry))
        elif e.code == "agent_audit":
            self.line(INDENT + "⚠ " + self._msg("notice.agent_audit", hits="; ".join(e.args.get("hits", []))[:200]))
        else:
            self.line(("⚠ " if e.level != "info" else "") + self._msg(f"notice.{e.code}", **e.args))

    def _on_DocEnd(self, e: DocEnd) -> None:
        self._set_live(None)
        o = e.outcome
        total = _total_cost(o)
        status_word = self._msg("done" if o.status == "complete" else "partial")
        if self.compact:
            note = f" · ⚠ {self._msg(f'why.{o.runtime_note}', detail=o.runtime_detail or '', seconds='')}" \
                if o.runtime == "hybrid:fallback" else ""
            self.line(self._msg("compact_line", mark="✓" if o.status == "complete" else "◐", source=o.source,
                                status=_value(o.status), pages=o.pages, open=o.review_open,
                                seconds=duration(self.lang, o.wall_s), cost=money(total, self.lang), note=note,
                                path=shown_path(o.markdown)))
            return
        self.line(pad(status_word, 6) + shown_path(o.markdown))
        self.line(INDENT + self._msg("result_line", status=_value(o.status),
                                     pages=o.pages, tables=o.tables, images=o.images, titles=o.titles,
                                     open=o.review_open))
        if o.missing:
            items = "; ".join(f"{m.block}（{m.reason}）" if self.lang == "zh" else f"{m.block} ({m.reason})"
                              for m in o.missing[:5])
            self.line(INDENT + self._msg("missing_line", n=len(o.missing), items=items))
        self.line(INDENT + self._msg("time_cost", seconds=duration(self.lang, o.wall_s), cost=self._cost(o)))
        if not self.quiet:
            images = " · images/" if o.images or (Path(o.out_dir) / "images").is_dir() else ""
            self.line(INDENT + self._msg("other_files", summary=Path(o.summary).name, blocks=Path(o.blocks).name,
                                         images=images))

    def _cost(self, o) -> str:
        agent = o.agent.usd_at_list_price if o.agent is not None else None
        total = _total_cost(o)
        if agent is not None:
            return self._msg("cost_split", total=money(total, self.lang), service=money(o.service_usd, self.lang),
                             agent=money(agent, self.lang))
        return self._msg("cost_service", total=money(total, self.lang))

    # ── results outside the event stream ─────────────────────────────

    def failure(self, source: str, code: str, message: str) -> None:
        self._set_live(None)
        key = f"error.{code}" if f"error.{code}" in _KNOWN_ERRORS else "error.other"
        self.line(pad(self._msg("failed"), 6) + f"{source}  " + self._msg(key, message=message))

    def interrupted(self, work: str | Path) -> None:
        self._set_live(None)
        self.line(self._msg("interrupted", work=shown_path(work)))

    def total(self, outcomes: list, failed: int, seconds: float) -> None:
        costs = [_total_cost(o) for o in outcomes]
        cost = None if any(c is None for c in costs) else sum(costs)
        self.line(self._msg("total", n=len(outcomes) + failed,
                            complete=sum(1 for o in outcomes if o.status == "complete"),
                            partial=sum(1 for o in outcomes if o.status != "complete"), failed=failed,
                            seconds=duration(self.lang, seconds), cost=money(cost, self.lang)))


_KNOWN_FAILURES = {"failure.service_error", "failure.timeout", "failure.budget_exhausted",
                   "failure.cache_miss_offline"}
_KNOWN_ERRORS = {"error.unreadable", "error.process_failed", "error.export_failed"}


def _value(status) -> str:
    return getattr(status, "value", status)


def _total_cost(o) -> float | None:
    agent = o.agent.usd_at_list_price if o.agent is not None else None
    if o.service_usd is None:
        return None
    return round(o.service_usd + (agent or 0.0), 4)

