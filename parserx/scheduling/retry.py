"""Which failures are retried, and how long to wait (guide §8.1: network, 5xx, 429, OCR queue full).

Only transport-level failures are retried here. A response that arrived but
cannot be parsed is re-requested by ``ServiceGateway.call(parse=...)``; a
parsed response is final even when it lacks the expected fields (§8.2 rule 3).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx2
import openai
import requests

from parserx.scheduling.errors import TransientError


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_s: float = 2.0
    max_backoff_s: float = 30.0

    def delay(self, attempt: int) -> float:
        """Wait before attempt ``attempt + 1``."""
        return min(self.backoff_s * 2 ** (attempt - 1), self.max_backoff_s)


def _retryable_status(status: int | None) -> bool:
    return status is not None and (status == 429 or status >= 500)


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TransientError):
        return True
    # httpx2 errors reach here raw when a streamed answer breaks off: the SDK only wraps the opening request
    if isinstance(exc, (openai.APIConnectionError, httpx2.TransportError, requests.ConnectionError,
                        requests.Timeout, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return _retryable_status(exc.status_code)
    if isinstance(exc, requests.HTTPError):
        return _retryable_status(exc.response.status_code if exc.response is not None else None)
    return False
