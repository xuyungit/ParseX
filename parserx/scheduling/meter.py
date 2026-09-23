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
from collections import Counter
from dataclasses import dataclass, field


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
              usd: float | None) -> None:
        with self._lock:
            tokens = self._tokens.setdefault(service, Counter())
            tokens["input"] += input_tokens
            tokens["cached_input"] += cached_input_tokens
            tokens["output"] += output_tokens
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
            )
