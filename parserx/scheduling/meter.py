"""Real request counting at the service boundary (guide §9.2 item 5).

Counts are recorded when a request is issued, not inferred from document
elements afterwards.  ``requests`` are real network calls (one batch OCR job
is one request carrying many pages); ``attempts`` are network attempts
reported by the services' retry and parameter-downgrade loops; cache hits and
offline misses are counted separately (see ``parserx.scheduling.gateway``).
Retries inside the OpenAI SDK are not visible here; Phase 1's scheduler takes
them over.
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

    def request(self, service: str, pages: int = 0) -> None:
        with self._lock:
            self._requests[service] += 1
            if pages:
                self._pages[service] += pages

    def attempt(self, service: str) -> None:
        with self._lock:
            self._attempts[service] += 1

    def cache_hit(self, service: str) -> None:
        with self._lock:
            self._cache_hits[service] += 1

    def cache_miss(self, service: str) -> None:
        with self._lock:
            self._cache_misses[service] += 1

    def snapshot(self) -> MeterSnapshot:
        with self._lock:
            return MeterSnapshot(
                requests=dict(self._requests),
                attempts=dict(self._attempts),
                pages=dict(self._pages),
                cache_hits=dict(self._cache_hits),
                cache_misses=dict(self._cache_misses),
            )
