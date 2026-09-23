"""Document budget (guide §3.3, §8.2): deadline, per-service requests, cost.

A request reserves room before it is sent and settles with its real cost
afterwards, so concurrent requests cannot overrun the budget together.  Cache
hits never reserve: they are not requests.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from parserx.scheduling.errors import BudgetExhausted


@dataclass(frozen=True)
class BudgetLimits:
    deadline_s: float | None = None
    requests: Mapping[str, int] = field(default_factory=dict)  # cap on real requests per service
    usd: float | None = None
    reserve_usd: Mapping[str, float] = field(default_factory=dict)  # held per request until its cost is known

    @classmethod
    def from_config(cls, config: Any) -> "BudgetLimits":
        return cls(deadline_s=config.deadline_s, requests=dict(config.requests), usd=config.usd,
                   reserve_usd=dict(config.reserve_usd))


@dataclass(frozen=True)
class Reservation:
    service: str
    usd: float


class Budget:
    def __init__(self, limits: BudgetLimits | None = None, *, clock: Callable[[], float] = time.monotonic):
        self.limits = limits or BudgetLimits()
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Start a new document: the clock and every counter restart."""
        with self._lock:
            self._start = self._clock()
            self._used: Counter[str] = Counter()
            self._in_flight: Counter[str] = Counter()
            self._spent = 0.0
            self._reserved = 0.0

    def reserve(self, service: str) -> Reservation:
        limits = self.limits
        with self._lock:
            if limits.deadline_s is not None and self._clock() - self._start >= limits.deadline_s:
                raise BudgetExhausted(service, f"deadline {limits.deadline_s}s reached")
            cap = limits.requests.get(service)
            if cap is not None and self._used[service] + self._in_flight[service] >= cap:
                raise BudgetExhausted(service, f"requests cap {cap} reached")
            hold = limits.reserve_usd.get(service, 0.0)
            if limits.usd is not None and self._spent + self._reserved + hold > limits.usd + 1e-12:
                raise BudgetExhausted(service, f"usd cap {limits.usd} reached")
            self._in_flight[service] += 1
            self._reserved += hold
            return Reservation(service, hold)

    def settle(self, reservation: Reservation, usd: float | None) -> None:
        """Book the request; an unknown cost is booked at its reservation."""
        with self._lock:
            self._in_flight[reservation.service] -= 1
            self._used[reservation.service] += 1
            self._reserved -= reservation.usd
            self._spent += reservation.usd if usd is None else usd

    def left(self) -> dict[str, Any]:
        limits = self.limits
        with self._lock:
            seconds = None
            if limits.deadline_s is not None:
                seconds = max(0.0, limits.deadline_s - (self._clock() - self._start))
            return {
                "requests": {s: max(0, cap - self._used[s] - self._in_flight[s]) for s, cap in sorted(limits.requests.items())},
                "usd": None if limits.usd is None else max(0.0, limits.usd - self._spent - self._reserved),
                "seconds": seconds,
            }
