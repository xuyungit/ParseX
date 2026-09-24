"""Concurrent requests, results applied in task order (guide §8.2 rule 2).

Phase 0 found v1 outputs that depended on which request finished first.
``run_ordered`` makes the fix general: *fetch* runs concurrently and must not
touch shared state; *apply* runs in the caller's thread, strictly in task
order, and only for tasks that succeeded.  Task inputs are fixed before any
request is sent.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Generic, Iterable, Literal, TypeVar

from parserx.cache import CacheMiss
from parserx.scheduling.errors import BudgetExhausted
from parserx.scheduling.retry import is_retryable

T = TypeVar("T")
R = TypeVar("R")

OutcomeStatus = Literal["ok", "failed", "skipped_budget", "cache_miss"]


@dataclass
class TaskOutcome(Generic[T, R]):
    index: int
    task: T
    status: OutcomeStatus
    value: R | None = None
    error: str | None = None
    retryable: bool = False
    exception: BaseException | None = field(default=None, repr=False)  # for callers that map failure types


def run_ordered(
    tasks: Iterable[T],
    fetch: Callable[[T], R],
    apply: Callable[[T, R], None] | None = None,
    *,
    max_workers: int = 4,
) -> list[TaskOutcome[T, R]]:
    tasks = list(tasks)
    if not tasks:
        return []
    outcomes: list[TaskOutcome[T, R]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(tasks)))) as pool:
        futures = [pool.submit(fetch, task) for task in tasks]
        for index, (task, future) in enumerate(zip(tasks, futures)):
            try:
                value = future.result()
            except BudgetExhausted as exc:
                outcomes.append(TaskOutcome(index, task, "skipped_budget", error=str(exc), exception=exc))
            except CacheMiss as exc:
                outcomes.append(TaskOutcome(index, task, "cache_miss", error=str(exc), exception=exc))
            except Exception as exc:
                outcomes.append(TaskOutcome(index, task, "failed", error=f"{type(exc).__name__}: {exc}",
                                            retryable=is_retryable(exc), exception=exc))
            else:
                if apply is not None:
                    apply(task, value)
                outcomes.append(TaskOutcome(index, task, "ok", value=value))
    return outcomes
