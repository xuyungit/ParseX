"""Where a document's time goes (speed plan §2): each step of the pipeline timed with what it asked of the services
and how long the local models (page reading, layout detection) ran in it.

Local work is timed by a process-wide clock (``LOCAL``): the readers are created in many places and need no handle
to a run.  Steps take the difference of two readings, so documents run one after another in a process are kept
apart; documents run side by side in one process share the clock and their step figures mix (timing only).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from parserx.ir.state import StepTime
from parserx.scheduling.meter import LOCAL, MeterSnapshot, RequestMeter


class StepClock:
    """Times named steps against one meter; ``steps`` in the order they ran (a step run twice is two entries).
    ``start(name)`` ends the step before it, ``stop()`` the last one: a long linear sequence is marked, not nested."""

    def __init__(self, meter: RequestMeter) -> None:
        self.meter = meter
        self.steps: list[StepTime] = []
        self._open: tuple[str, float, MeterSnapshot, dict] | None = None

    def start(self, name: str) -> None:
        self.stop()
        self._open = (name, time.perf_counter(), self.meter.snapshot(), LOCAL.snapshot())

    def stop(self) -> None:
        if self._open is None:
            return
        name, t, meter, local = self._open
        self._open = None
        self.steps.append(step_time(name, time.perf_counter() - t, meter, self.meter.snapshot(), local,
                                    LOCAL.snapshot()))

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        self.start(name)
        try:
            yield
        finally:
            self.stop()


def step_time(name: str, seconds: float, before: MeterSnapshot, after: MeterSnapshot,
              local_before: dict[str, tuple[int, float]], local_after: dict[str, tuple[int, float]]) -> StepTime:
    def minus(a: dict, b: dict) -> dict:
        return {k: v - b.get(k, 0) for k, v in sorted(a.items()) if v - b.get(k, 0)}

    models = {}
    for model, used in after.models.items():
        diff = minus(used, before.models.get(model, {}))
        if diff:
            models[model] = {k: (round(v, 6) if k == "usd" else int(v)) for k, v in diff.items()}
    local = {}
    for kind, (calls, secs) in sorted(local_after.items()):
        c0, s0 = local_before.get(kind, (0, 0.0))
        if calls - c0:
            local[kind] = {"calls": calls - c0, "s": round(secs - s0, 2)}
    usd = sum(m.get("usd", 0.0) for m in models.values())
    return StepTime(step=name, s=round(seconds, 2), requests=minus(after.requests, before.requests),
                    pages=minus(after.pages, before.pages), cache_hits=minus(after.cache_hits, before.cache_hits),
                    models=models, local=local, usd=round(usd, 6))
