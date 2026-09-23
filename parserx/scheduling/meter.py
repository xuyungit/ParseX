"""Real request counting at the service boundary (guide §9.2 item 5).

Counts are recorded when a request is issued, not inferred from document
elements afterwards.  ``requests`` are logical calls (one batch OCR job is one
request carrying many pages); ``attempts`` are network attempts reported by
the services' retry and parameter-downgrade loops.  Retries inside the OpenAI
SDK are not visible here; Phase 1's scheduler takes them over.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Service methods that issue one logical request each.
_REQUEST_METHODS = frozenset({"complete", "describe_image", "describe_images", "recognize", "recognize_pdf"})


@dataclass(frozen=True)
class MeterSnapshot:
    requests: dict[str, int] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    pages: dict[str, int] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)


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

    def snapshot(self) -> MeterSnapshot:
        with self._lock:
            return MeterSnapshot(
                requests=dict(self._requests),
                attempts=dict(self._attempts),
                pages=dict(self._pages),
                cache_hits=dict(self._cache_hits),
            )


class MeteredService:
    """Wraps an OCR / LLM / VLM service; same interface, every request counted."""

    def __init__(self, inner: Any, meter: RequestMeter, service: str) -> None:
        self._inner = inner
        self._meter = meter
        self._service = service
        if hasattr(inner, "attempt_hook"):
            inner.attempt_hook = lambda: meter.attempt(service)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _REQUEST_METHODS or not callable(attr):
            return attr

        def counted(*args: Any, **kwargs: Any) -> Any:
            self._meter.request(self._service, pages=_pages_for(name, args, kwargs))
            return attr(*args, **kwargs)

        return counted


def _pages_for(method: str, args: tuple, kwargs: dict) -> int:
    if method == "recognize":
        return 1
    if method == "recognize_pdf":
        pdf_bytes = args[0] if args else kwargs.get("pdf_bytes", b"")
        import fitz  # PyMuPDF; local import keeps the meter light for non-OCR users

        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                return doc.page_count
        except Exception:
            return 0
    return 0
