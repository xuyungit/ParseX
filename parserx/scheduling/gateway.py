"""The single path every remote request takes (guide §8.2–§8.3).

``ServiceGateway.call`` looks the request up in the response cache, counts a
hit, a miss (offline replay) or a real request, performs the call on a miss
and records the response.  Two entry points use it:

- ``MeteredService`` wraps an LLM / VLM service at its public methods and
  caches the final answer string;
- ``PaddleOCRService.gateway`` is used at the OCR client's single transport
  exit and caches the raw merged JSON, so response parsing still runs.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from typing import Any, Callable

from parserx.cache import CacheMiss, ResponseCache, digest_arguments, request_key
from parserx.scheduling.meter import RequestMeter

log = logging.getLogger(__name__)

# LLM / VLM methods that issue one logical request each.
_REQUEST_METHODS = frozenset({"complete", "describe_image", "describe_images"})


class ServiceGateway:
    def __init__(self, meter: RequestMeter, cache: ResponseCache | None = None) -> None:
        self._meter = meter
        self._cache = cache

    def call(
        self,
        service: str,
        material: dict[str, Any],
        fetch: Callable[[], Any],
        *,
        pages: int = 0,
    ) -> Any:
        cache = self._cache
        if cache is None:
            self._meter.request(service, pages=pages)
            return fetch()

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

        self._meter.request(service, pages=pages)
        response = fetch()
        cache.put(service, key, response, material)
        return response


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
    ) -> None:
        self._inner = inner
        self._gateway = ServiceGateway(meter, cache)
        self._service = service
        self._identity = identity or {}
        if hasattr(inner, "attempt_hook"):
            inner.attempt_hook = lambda: meter.attempt(service)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _REQUEST_METHODS or not callable(attr):
            return attr

        def through_gateway(*args: Any, **kwargs: Any) -> Any:
            bound = inspect.signature(attr).bind(*args, **kwargs)
            bound.apply_defaults()
            material = {
                "method": name,
                **self._identity,
                "args": digest_arguments(dict(bound.arguments)),
            }
            return self._gateway.call(self._service, material, lambda: attr(*args, **kwargs))

        return through_gateway
