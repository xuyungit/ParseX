"""Inline emphasis scored apart from the text (R3, metric 2.4; report only).

Bold (``**…**``, ``<b>``, ``<strong>``) and underline (``<u>``) spans of the output are matched with those of the
annotation on their letters and digits (``reading.compare.normalize``); precision and recall per kind, None when
neither side has a span of that kind.  ParserX's own labels (the image-text label) are not emphasis of the document.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from parserx.reading.compare import normalize

_BOLD = re.compile(r"\*\*(.+?)\*\*|<(?:b|strong)>(.+?)</(?:b|strong)>", re.IGNORECASE)
_UNDERLINE = re.compile(r"<u>(.+?)</u>", re.IGNORECASE)
_OWN_LABELS = {normalize(t) for t in ("〔图片识别〕", "[Text from image]")}


@dataclass
class KindMetrics:
    expected: int = 0
    output: int = 0
    matched: int = 0

    @property
    def precision(self) -> float | None:
        return self.matched / self.output if self.output else (None if not self.expected else 0.0)

    @property
    def recall(self) -> float | None:
        return self.matched / self.expected if self.expected else (None if not self.output else 0.0)


@dataclass
class FormatMetrics:
    bold: KindMetrics = field(default_factory=KindMetrics)
    underline: KindMetrics = field(default_factory=KindMetrics)


def compute_format_metrics(output_md: str, expected_md: str) -> FormatMetrics:
    return FormatMetrics(bold=_kind(_BOLD, output_md, expected_md), underline=_kind(_UNDERLINE, output_md, expected_md))


def _kind(pattern: re.Pattern, output_md: str, expected_md: str) -> KindMetrics:
    out, exp = _spans(pattern, output_md), _spans(pattern, expected_md)
    return KindMetrics(expected=sum(exp.values()), output=sum(out.values()), matched=sum((out & exp).values()))


def _spans(pattern: re.Pattern, markdown: str) -> Counter[str]:
    spans: Counter[str] = Counter()
    for match in pattern.finditer(markdown):
        text = normalize(next(g for g in match.groups() if g is not None))
        if text and text not in _OWN_LABELS:
            spans[text] += 1
    return spans
