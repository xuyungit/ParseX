"""Arithmetic consistency of a recognized table (plan P2-7, round 1 finding G1; guide §9.3 silent errors).

A misread digit in a table is invisible to every text check.  Tables often carry their own redundancy: an amount
column that is the product of two others, a total row that is the sum of the rows above.  When such a relation
holds in most rows and fails in one, that row probably holds a misread digit — a thing to look at on the image, not
a correction.  Relations are found from the numbers alone (no header words):

- **product**: columns a, b, c with a × b = c in at least three rows and at least 60 % of the rows where all three
  are numbers; the rows where it fails are reported.  A row where a factor is 1 proves nothing (the product is a
  copy), nor does a c that mostly equals a or b.
- **total**: a data row where at least two columns equal the sum of the rows above it (since the previous total);
  every other column of that row whose value is not that sum is reported — except a key column (integers that
  strictly increase down the table: numbers, sizes, years), which a real total row labels instead of summing.

Values are plain numbers (thousands separators allowed); percentages, dates and text are ignored.  A result
agrees when it is within half a unit of its last printed decimal.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from itertools import permutations

from parserx.tables.grid import TableGrid

_NUMBER = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_MIN_ROWS = 3
_SHARE = 0.6


def arithmetic_issues(grid: TableGrid) -> list[str]:
    values = _values(grid)
    rows = sorted({r for r, _ in values if r >= grid.header_rows})
    cols = sorted({c for _, c in values})
    return _product_issues(values, rows, cols) + _total_issues(values, rows, cols)


def _values(grid: TableGrid) -> dict[tuple[int, int], Decimal]:
    out = {}
    for cell in grid.cells:
        text = "".join(cell.content.split())
        if cell.row >= grid.header_rows and _NUMBER.match(text):
            try:
                out[(cell.row, cell.col)] = Decimal(text.replace(",", ""))
            except InvalidOperation:
                continue
    return out


def _agrees(computed: Decimal, printed: Decimal) -> bool:
    exponent = printed.as_tuple().exponent
    unit = Decimal(1).scaleb(exponent if isinstance(exponent, int) else 0)
    return abs(computed - printed) <= unit / 2


def _product_issues(values, rows, cols) -> list[str]:
    best = None
    for a, b, c in permutations(cols, 3):
        if a > b:
            continue  # a × b = b × a
        full = [r for r in rows if (r, a) in values and (r, b) in values and (r, c) in values]
        if len(full) < _MIN_ROWS or all(values[(r, a)] == 1 for r in full) or all(values[(r, b)] == 1 for r in full):
            continue
        if sum(values[(r, c)] in (values[(r, a)], values[(r, b)]) for r in full) * 2 > len(full):
            continue  # c is mostly a copy of a factor
        good = [r for r in full if _agrees(values[(r, a)] * values[(r, b)], values[(r, c)])]
        bad = [r for r in full if r not in good]
        proving = [r for r in good if values[(r, a)] != 1 and values[(r, b)] != 1]
        if len(proving) >= _MIN_ROWS and len(good) >= _SHARE * len(full) and bad:
            if best is None or len(good) > len(best[3]):
                best = (a, b, c, good, bad)
    if best is None:
        return []
    a, b, c, good, bad = best
    return [f"row {r}: col {a} × col {b} = {_fmt(values[(r, a)] * values[(r, b)])} but col {c} has "
            f"{_fmt(values[(r, c)])} ({len(good)} other rows agree); a digit may be misread" for r in bad]


def _total_issues(values, rows, cols) -> list[str]:
    issues: list[str] = []
    start = 0  # rows before a total belong to it
    for i, r in enumerate(rows):
        above = rows[start:i]
        if len(above) < 2:
            continue
        sums = {c: sum(values[(x, c)] for x in above if (x, c) in values) for c in cols
                if (r, c) in values and sum((x, c) in values for x in above) >= 2}
        agreeing = [c for c, s in sums.items() if _agrees(s, values[(r, c)])]
        if len(agreeing) < 2:
            continue
        issues += [f"row {r}: col {c} has {_fmt(values[(r, c)])} but the rows above sum to {_fmt(s)} "
                   f"(cols {', '.join(map(str, agreeing))} of this row are their sums); a digit may be misread"
                   for c, s in sums.items() if c not in agreeing and not _key_column(values, rows, c)]
        start = i + 1
    return issues


def _key_column(values, rows, col) -> bool:
    column = [values[(r, col)] for r in rows if (r, col) in values]
    return len(column) >= _MIN_ROWS and all(v == v.to_integral_value() for v in column) \
        and all(x < y for x, y in zip(column, column[1:]))


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")
