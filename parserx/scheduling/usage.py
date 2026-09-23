"""Token prices (guide §10.3) and cost of one request.

Prices are USD per million tokens, keyed by model name, from the
``scheduling.prices`` config.  OCR is billed per page outside this table and
is not part of ``cost_usd``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cached_input: float = 0.0


class PriceTable:
    def __init__(self, prices: Mapping[str, Price]):
        self._prices = dict(prices)

    @classmethod
    def from_config(cls, prices: Mapping[str, Any]) -> "PriceTable":
        return cls({model: Price(input=p.input, output=p.output, cached_input=p.cached_input)
                    for model, p in prices.items()})

    def cost(self, model: str, *, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> float | None:
        price = self._prices.get(model)
        if price is None:
            return None
        uncached = max(0, input_tokens - cached_input_tokens)
        return (uncached * price.input + cached_input_tokens * price.cached_input
                + output_tokens * price.output) / 1e6
