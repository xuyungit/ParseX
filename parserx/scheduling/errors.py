"""Failure types the scheduling layer distinguishes (guide §8.1–§8.2)."""

from __future__ import annotations


class TransientError(RuntimeError):
    """A service-side condition worth another network attempt (5xx, queue full, lost job)."""


class UnparseableResponse(ValueError):
    """The response arrived but cannot be interpreted; handled by a fresh request, not a transport retry."""


class PageCountMismatch(RuntimeError):
    """A batch OCR job returned a different number of pages than were submitted."""

    def __init__(self, expected: int, got: int):
        super().__init__(f"OCR returned {got} pages for {expected} submitted")
        self.expected = expected
        self.got = got


class BudgetExhausted(RuntimeError):
    """The document budget has no room for this request; the task ends as skipped_budget."""

    def __init__(self, service: str, reason: str):
        super().__init__(f"{service} budget exhausted: {reason}")
        self.service = service
        self.reason = reason
