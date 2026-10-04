"""Real request counting at the service boundary (guide §9.2 item 5).

Counts are recorded when a request is issued, not inferred from document
elements afterwards.  ``requests`` are real network calls (one batch OCR job
is one request carrying many pages); ``attempts`` are network attempts,
including retries and parameter-downgrade resends; cache hits, offline misses
and budget skips are counted separately (see ``parserx.scheduling.gateway``).
Token usage and its cost are recorded per service; ``cost_usd`` is None when
some usage had no price (unknown, not free).
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class MeterSnapshot:
    requests: dict[str, int] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    pages: dict[str, int] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)
    cache_misses: dict[str, int] = field(default_factory=dict)  # offline replay only
    skipped_budget: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, dict[str, int]] = field(default_factory=dict)  # service → input / cached_input / output
    cost_usd: float | None = 0.0
    # model → calls / input / cached_input / output / usd (priced calls only), for where the money goes
    models: dict[str, dict[str, float]] = field(default_factory=dict)


class RequestMeter:
    """Thread-safe per-service counters for one document run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._requests: Counter[str] = Counter()
            self._attempts: Counter[str] = Counter()
            self._pages: Counter[str] = Counter()
            self._cache_hits: Counter[str] = Counter()
            self._cache_misses: Counter[str] = Counter()
            self._skipped: Counter[str] = Counter()
            self._tokens: dict[str, Counter[str]] = {}
            self._models: dict[str, Counter[str]] = {}
            self._cost = 0.0
            self._unpriced = False

    def request(self, service: str, pages: int = 0) -> None:
        with self._lock:
            self._requests[service] += 1
            if pages:
                self._pages[service] += pages

    def attempt(self, service: str, count: int = 1) -> None:
        with self._lock:
            self._attempts[service] += count

    def cache_hit(self, service: str) -> None:
        with self._lock:
            self._cache_hits[service] += 1

    def cache_miss(self, service: str) -> None:
        with self._lock:
            self._cache_misses[service] += 1

    def skipped_budget(self, service: str) -> None:
        with self._lock:
            self._skipped[service] += 1

    def usage(self, service: str, *, input_tokens: int, cached_input_tokens: int, output_tokens: int,
              usd: float | None, model: str | None = None) -> None:
        with self._lock:
            tokens = self._tokens.setdefault(service, Counter())
            tokens["input"] += input_tokens
            tokens["cached_input"] += cached_input_tokens
            tokens["output"] += output_tokens
            if model:
                used = self._models.setdefault(model, Counter())
                used["calls"] += 1
                used["input"] += input_tokens
                used["cached_input"] += cached_input_tokens
                used["output"] += output_tokens
                used["usd"] += usd or 0.0
            if usd is None:
                self._unpriced = True
            else:
                self._cost += usd

    def snapshot(self) -> MeterSnapshot:
        with self._lock:
            return MeterSnapshot(
                requests=dict(self._requests),
                attempts=dict(self._attempts),
                pages=dict(self._pages),
                cache_hits=dict(self._cache_hits),
                cache_misses=dict(self._cache_misses),
                skipped_budget=dict(self._skipped),
                tokens={s: {k: t[k] for k in ("input", "cached_input", "output")}
                        for s, t in sorted(self._tokens.items())},
                cost_usd=None if self._unpriced else round(self._cost, 8),
                models={m: dict(c) for m, c in sorted(self._models.items())},
            )


class LocalClock:
    """Calls and seconds of local model work by kind (``reading``, ``layout``), process-wide: the local readers are
    created in many places with no handle to a run; steps take differences (``scheduling/timing.py``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: Counter[str] = Counter()
        self._seconds: Counter[str] = Counter()

    @contextmanager
    def time(self, kind: str) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self._calls[kind] += 1
                self._seconds[kind] += time.perf_counter() - t

    def snapshot(self) -> dict[str, tuple[int, float]]:
        with self._lock:
            return {k: (self._calls[k], self._seconds[k]) for k in self._calls}


LOCAL = LocalClock()
