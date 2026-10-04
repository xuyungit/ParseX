"""Scheduling layer (guide §8.2): the gateway every remote request takes, with cache,
counting, budgets, token cost, transport retries, ordered results and OCR job resume."""

from parserx.scheduling.budget import Budget, BudgetLimits, Reservation
from parserx.scheduling.errors import BudgetExhausted, PageCountMismatch, TransientError, UnparseableResponse
from parserx.scheduling.gateway import MeteredService, ServiceGateway
from parserx.scheduling.jobs import JobStore
from parserx.scheduling.meter import MeterSnapshot, RequestMeter
from parserx.scheduling.ordered import TaskOutcome, run_ordered, spread
from parserx.scheduling.retry import RetryPolicy, is_retryable
from parserx.scheduling.usage import Price, PriceTable

__all__ = [
    "Budget",
    "BudgetExhausted",
    "BudgetLimits",
    "JobStore",
    "MeterSnapshot",
    "MeteredService",
    "PageCountMismatch",
    "Price",
    "PriceTable",
    "RequestMeter",
    "Reservation",
    "RetryPolicy",
    "ServiceGateway",
    "TaskOutcome",
    "TransientError",
    "UnparseableResponse",
    "is_retryable",
    "run_ordered",
    "spread",
]
