"""Key-content error counts (metric version 2.0, guide §9.2 item 4).

Four kinds of tokens whose corruption changes meaning even when character
scores barely move: numbers, numbers with units, negation words and dates.
Each kind is extracted in reading order from both sides; the token sequences
are aligned by LCS and the unaligned tokens are reported as missing
(expected only) or extra (output only).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz.distance import LCSseq

from parserx.eval.normalize import canonicalize

KINDS = ("number", "unit", "negation", "date")

_DATE_RE = re.compile(
    r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月(?:\s*(?P<d>\d{1,2})\s*日)?"
    r"|(?P<y2>\d{4})[-/.](?P<m2>\d{1,2})[-/.](?P<d2>\d{1,2})(?!\d)"
)
_NUMBER = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_CJK_UNITS = (
    "万元 亿元 千米 厘米 毫米 公里 千克 公斤 毫升 分钟 小时 平方米 立方米 "
    "万 亿 元 米 克 吨 升 秒 天 年 月 日 次 片 个 件 台 套 张 页 度 倍 人 项 条 位 岁 块 分"
).split()
_UNIT = "|".join(
    [re.escape(u) for u in sorted(_CJK_UNITS, key=len, reverse=True)]
    + [r"%", r"‰", r"℃", r"°C?", r"[A-Za-zμΩ]{1,4}(?![A-Za-z])"]
)
_NUMBER_RE = re.compile(_NUMBER)
_UNIT_RE = re.compile(rf"(?P<n>{_NUMBER})\s?(?P<u>{_UNIT})")
_NEGATION_RE = re.compile(r"[不没无未非否勿莫]|\b(?:not|no|never|none|without|cannot)\b|n't", re.IGNORECASE)


@dataclass
class KeyContentMetrics:
    missing: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    extra: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))

    @property
    def total(self) -> int:
        return sum(self.missing.values()) + sum(self.extra.values())


def extract_key_tokens(markdown: str) -> dict[str, list[str]]:
    text = unicodedata.normalize("NFKC", canonicalize(markdown).text)
    dates: list[str] = []

    def take_date(match: re.Match) -> str:
        y = match.group("y") or match.group("y2")
        m = match.group("m") or match.group("m2")
        d = match.group("d") or match.group("d2")
        dates.append(f"{int(y):04d}-{int(m):02d}" + (f"-{int(d):02d}" if d else ""))
        return " " * len(match.group(0))

    text = _DATE_RE.sub(take_date, text)
    return {
        "number": [_plain(m.group(0)) for m in _NUMBER_RE.finditer(text)],
        "unit": [_plain(m.group("n")) + m.group("u") for m in _UNIT_RE.finditer(text)],
        "negation": [m.group(0).lower() for m in _NEGATION_RE.finditer(text)],
        "date": dates,
    }


def compute_key_content_errors(output: str, expected: str) -> KeyContentMetrics:
    out_tokens = extract_key_tokens(output)
    exp_tokens = extract_key_tokens(expected)
    metrics = KeyContentMetrics()
    for kind in KINDS:
        common = LCSseq.similarity(exp_tokens[kind], out_tokens[kind])
        metrics.missing[kind] = len(exp_tokens[kind]) - common
        metrics.extra[kind] = len(out_tokens[kind]) - common
    return metrics


def _plain(number: str) -> str:
    return number.replace(",", "")
