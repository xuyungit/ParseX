"""Reading-order metric (metric version 2.0, guide §9.2 item 3).

The expected text is split into blocks on blank lines (a table is one block).
Each block is located in the output through its anchors: character n-grams
that occur exactly once in the expected text and exactly once in the output.
A block's position is the median output offset of its anchors, which makes
the locator insensitive to line wrapping yet able to see moved blocks.
Over the located blocks we count pairwise inversions and report Kendall's
tau (1 − 2 · inversion rate) plus coverage (share of locatable blocks —
those with at least one unique anchor — found in the output).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from statistics import median

from parserx.eval.normalize import canonicalize, char_sequence

ANCHOR_LEN = 8
_BLANK_LINE_RE = re.compile(r"\n\s*\n")


@dataclass
class OrderMetrics:
    tau: float | None = None  # None: fewer than two blocks located
    inversions: int = 0
    blocks_located: int = 0
    blocks_total: int = 0
    coverage: float | None = None


def compute_order_metrics(output: str, expected: str) -> OrderMetrics:
    exp_text = canonicalize(expected).text
    blocks = [
        seq
        for seq in (char_sequence(b) for b in _BLANK_LINE_RE.split(exp_text))
        if len(seq) >= ANCHOR_LEN
    ]
    # Uniqueness is judged on the full character sequence on both sides, so a
    # perfect output locates every block that has a unique anchor.
    exp_counts = Counter(_grams(char_sequence(exp_text)))
    # Blocks whose text all repeats elsewhere have no unique anchor and cannot
    # be located even in a perfect output; they are left out of the ratio.
    blocks = [b for b in blocks if any(exp_counts[g] == 1 for g in _grams(b))]
    if not blocks:
        return OrderMetrics()

    out_grams = _grams(char_sequence(canonicalize(output).text))
    out_counts = Counter(out_grams)
    out_pos = {g: i for i, g in enumerate(out_grams) if out_counts[g] == 1}

    positions: list[float] = []
    for block in blocks:
        hits = [out_pos[g] for g in _grams(block) if exp_counts[g] == 1 and g in out_pos]
        if hits:
            positions.append(median(hits))

    located = len(positions)
    pairs = located * (located - 1) // 2
    inversions = _count_inversions(positions)
    return OrderMetrics(
        tau=round(1 - 2 * inversions / pairs, 4) if pairs else None,
        inversions=inversions,
        blocks_located=located,
        blocks_total=len(blocks),
        coverage=round(located / len(blocks), 4),
    )


def _grams(seq: str) -> list[str]:
    return [seq[i:i + ANCHOR_LEN] for i in range(len(seq) - ANCHOR_LEN + 1)]


def _count_inversions(values: list[float]) -> int:
    """Pairs (i < j) with values[i] > values[j], by merge sort."""
    if len(values) < 2:
        return 0
    mid = len(values) // 2
    left, right = values[:mid], values[mid:]
    count = _count_inversions(left) + _count_inversions(right)
    left.sort()
    right.sort()
    j = 0
    for x in left:
        while j < len(right) and right[j] < x:
            j += 1
        count += j
    return count
