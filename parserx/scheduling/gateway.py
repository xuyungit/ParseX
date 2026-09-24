"""The single path every remote request takes (guide §8.2–§8.3).

``ServiceGateway.call`` looks the request up in the response cache; on a miss
it reserves budget, sends the request with transport retries, meters requests,
attempts, tokens and cost, settles the budget and records the response.
Two entry points use it:

- ``MeteredService`` wraps an LLM / VLM service at its public methods and
  caches the final answer string;
- ``PaddleOCRService`` uses it at the OCR client's single transport exit and
  caches the raw merged JSON, so response parsing still runs.

Services report network sends and token usage through hooks bound to the
gateway; a context variable attributes them to the request being made, so
concurrent requests on one service never mix their counts.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from parserx.cache import CacheMiss, ResponseCache, digest_arguments, request_key
from parserx.scheduling.budget import Budget, BudgetLimits
from parserx.scheduling.errors import BudgetExhausted, UnparseableResponse
from parserx.scheduling.meter import RequestMeter
from parserx.scheduling.retry import RetryPolicy, is_retryable
from parserx.scheduling.usage import PriceTable

log = logging.getLogger(__name__)

# LLM / VLM methods that issue one logical request each.
_REQUEST_METHODS = frozenset({"complete", "describe_image", "describe_images"})


@dataclass
class _Attempt:
    sends: int = 0
    usage: list[tuple[str, int, int, int]] = field(default_factory=list)


_CURRENT: ContextVar[_Attempt | None] = ContextVar("parserx_gateway_attempt", default=None)


class ServiceGateway:
    def __init__(
        self,
        meter: RequestMeter,
        cache: ResponseCache | None = None,
        *,
        budget: Budget | None = None,
        retry: RetryPolicy | None = None,
        prices: PriceTable | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._meter = meter
        self._cache = cache
        self.budget = budget
        self._retry = retry or RetryPolicy()
        self._prices = prices
        self._sleep = sleep or time.sleep

    @classmethod
    def from_config(cls, meter: RequestMeter, cache: ResponseCache | None, scheduling: Any) -> "ServiceGateway":
        """Gateway with the budget, retry policy and prices of a ``SchedulingConfig``."""
        retry = scheduling.retry
        return cls(
            meter, cache,
            budget=Budget(BudgetLimits.from_config(scheduling.budget)),
            retry=RetryPolicy(retry.max_attempts, retry.backoff_s, retry.max_backoff_s),
            prices=PriceTable.from_config(scheduling.prices),
        )

    # ── Hooks for services ──────────────────────────────────────────────

    def record_send(self) -> None:
        """A service sent one network request (e.g. a parameter-downgrade resend)."""
        current = _CURRENT.get()
        if current is not None:
            current.sends += 1

    def record_usage(self, model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> None:
        current = _CURRENT.get()
        if current is not None:
            current.usage.append((model, input_tokens, cached_input_tokens, output_tokens))

    # ── Requests ────────────────────────────────────────────────────────

    def call(
        self,
        service: str,
        material: dict[str, Any],
        fetch: Callable[[], Any],
        *,
        pages: int = 0,
        parse: Callable[[Any], Any] | None = None,
        parse_retries: int = 1,
    ) -> Any:
        """The response, or ``parse(response)`` when *parse* is given.

        A response *parse* rejects with ``UnparseableResponse`` is requested
        again as a distinct request (``parse_retry`` in its key), so a replay
        walks through the same sequence of responses.
        """
        rounds = parse_retries + 1 if parse is not None else 1
        for n in range(rounds):
            round_material = material if n == 0 else {**material, "parse_retry": n}
            response = self._obtain(service, round_material, fetch, pages)
            if parse is None:
                return response
            try:
                return parse(response)
            except UnparseableResponse as exc:
                if n == rounds - 1:
                    raise
                log.warning("%s response unparseable (%s); requesting again", service, exc)
        raise AssertionError("unreachable")

    def _obtain(self, service: str, material: dict[str, Any], fetch: Callable[[], Any], pages: int) -> Any:
        cache = self._cache
        key = None
        if cache is not None:
            key = request_key(service, material)
            hit, response = cache.get(service, key)
            if hit:
                self._meter.cache_hit(service)
                return response
            if os.environ.get("PARSERX_CACHE_DEBUG"):
                log.warning("cache miss %s %s: %s", service, key[:12],
                            json.dumps(material, ensure_ascii=False, default=str)[:2000])
            if cache.offline:
                self._meter.cache_miss(service)
                raise CacheMiss(service, key)

        reservation = None
        if self.budget is not None:
            try:
                reservation = self.budget.reserve(service)
            except BudgetExhausted:
                self._meter.skipped_budget(service)
                raise
        self._meter.request(service, pages=pages)
        spent: list[float | None] = []
        try:
            response = self._send(service, fetch, spent)
        finally:
            if reservation is not None:
                self.budget.settle(reservation, None if None in spent else sum(spent))
        if cache is not None:
            cache.put(service, key, response, material)
        return response

    def _send(self, service: str, fetch: Callable[[], Any], spent: list[float | None]) -> Any:
        attempt = 0
        while True:
            attempt += 1
            current = _Attempt()
            token = _CURRENT.set(current)
            try:
                return fetch()
            except Exception as exc:
                if attempt >= self._retry.max_attempts or not is_retryable(exc):
                    raise
                wait = self._retry.delay(attempt)
                log.warning("%s attempt %d failed (%s: %s); retrying in %.1fs",
                            service, attempt, type(exc).__name__, exc, wait)
            finally:
                _CURRENT.reset(token)
                self._meter.attempt(service, max(1, current.sends))
                spent.extend(self._book_usage(service, current.usage))
            self._sleep(wait)

    def _book_usage(self, service: str, usage: list[tuple[str, int, int, int]]) -> list[float | None]:
        costs: list[float | None] = []
        for model, input_tokens, cached, output_tokens in usage:
            usd = None if self._prices is None else self._prices.cost(
                model, input_tokens=input_tokens, cached_input_tokens=cached, output_tokens=output_tokens)
            self._meter.usage(service, input_tokens=input_tokens, cached_input_tokens=cached,
                              output_tokens=output_tokens, usd=usd)
            costs.append(usd)
        return costs


class MeteredService:
    """Wraps an LLM / VLM service; same interface, every request goes through the gateway."""

    def __init__(
        self,
        inner: Any,
        meter: RequestMeter,
        service: str,
        *,
        cache: ResponseCache | None = None,
        identity: dict[str, Any] | None = None,
        gateway: ServiceGateway | None = None,
    ) -> None:
        self._inner = inner
        self._gateway = gateway or ServiceGateway(meter, cache)
        self._service = service
        self._identity = identity or {}
        if hasattr(inner, "attempt_hook"):
            inner.attempt_hook = self._gateway.record_send
        if hasattr(inner, "usage_hook"):
            inner.usage_hook = self._gateway.record_usage

    def call(self, method: str, *args: Any, parse: Callable[[Any], Any] | None = None, **kwargs: Any) -> Any:
        """One request method through the gateway, with an optional parser (see ``ServiceGateway.call``)."""
        attr = getattr(self._inner, method)
        material = self._material(method, args, kwargs)
        return self._gateway.call(self._service, material, lambda: attr(*args, **kwargs), parse=parse)

    def request_key(self, method: str, *args: Any, **kwargs: Any) -> str:
        """Cache key of this request (recorded as an Observation's ``raw_ref``)."""
        return request_key(self._service, self._material(method, args, kwargs))

    def _material(self, method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
        bound = inspect.signature(getattr(self._inner, method)).bind(*args, **kwargs)
        bound.apply_defaults()
        return {"method": method, **self._identity, "args": digest_arguments(dict(bound.arguments))}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _REQUEST_METHODS or not callable(attr):
            return attr

        def through_gateway(*args: Any, **kwargs: Any) -> Any:
            material = self._material(name, args, kwargs)
            return self._gateway.call(self._service, material, lambda: attr(*args, **kwargs))

        return through_gateway
