"""Matrix elements by row and column (from the unified evaluator, round 2: conventions §3.3 and §4 item 7).

A formula metric compares a matrix's characters, so an element written in the wrong column (round 1, paper_chn02:
``b_n`` one column to the left) costs a character or two.  Here every matrix environment (``matrix``, ``pmatrix``,
``bmatrix``, ``Bmatrix``, ``vmatrix``, ``Vmatrix``, ``smallmatrix``, ``array``, ``cases``, ``dcases``) of both sides is
read as a grid —
rows split at ``\\\\``, columns at ``&``, each element in the formula metric's one notation (``formulas.notation``).
An annotated matrix is paired with the output matrix whose elements it shares most (as tables are paired); rows and
columns are aligned as a table's are; an annotated non-empty element is in place when the aligned position of the
paired matrix holds the same element.  A nested matrix is a matrix of its own; in its parent it is one element.

The opportunities are the annotation's: every non-empty element of its matrices.  An annotated matrix the output does
not have (left as an image, written as text) has all its elements missing.  An output element not in place — in a
row or column the annotation does not have, or anywhere in a matrix the annotation does not have — is extra; and a
paired matrix of another size (rows, or columns up to the last one holding an element) is reported.  So a column
added to a correct matrix costs its elements, not the whole matrix (the other team's review: alignment alone let a
column of zeros added on the left read 4/4).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from parserx.eval.formulas import notation
from parserx.eval.normalize import _MATH_RE
from parserx.eval.tables import _MIN_PAIR_SIMILARITY, _align, _dice

_ENV_RE = re.compile(r"\\(begin|end)\{(matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|smallmatrix|array|d?cases)\}")
_ARRAY_SPEC_RE = re.compile(r"^\s*\{[^{}]*\}")
_ROW_BREAK_RE = re.compile(r"\\\\(?:\s*\[[^\]]*\])?")


@dataclass
class MatrixMetrics:
    expected: int = 0  # the annotation's matrices
    paired: int = 0
    elements: int = 0  # non-empty elements of the annotation's matrices: the opportunities
    in_place: int = 0
    extra: int = 0  # output elements not in place (in paired matrices) or in an output matrix with no pair
    other_size: int = 0  # paired matrices whose rows or columns differ in number


def _split_top(text: str, separator: re.Pattern) -> list[str]:
    """*text* split at *separator* where it stands outside braces and nested environments."""
    parts, depth, env, cursor, i = [], 0, 0, 0, 0
    while i < len(text):
        if text[i] == "\\" and (m := _ENV_RE.match(text, i)):
            env += 1 if m.group(1) == "begin" else -1
            i = m.end()
            continue
        if depth == 0 and env == 0 and (m := separator.match(text, i)):
            parts.append(text[cursor:i])
            cursor = i = m.end()
            continue
        if text[i] == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    parts.append(text[cursor:])
    return parts


def _element(text: str) -> str:
    return re.sub(r"\s+", "", notation(text))


def matrices(markdown: str) -> list[list[list[str]]]:
    """Every matrix environment in the math of *markdown*, outermost first, as rows of elements."""
    found = []
    for math in (next(g for g in m.groups() if g is not None) for m in _MATH_RE.finditer(markdown)):
        stack = []
        for m in _ENV_RE.finditer(math):
            if m.group(1) == "begin":
                stack.append((m.group(2), m.end()))
            elif stack:
                name, start = stack.pop()
                body = math[start:m.start()]
                if name == "array":
                    body = _ARRAY_SPEC_RE.sub("", body, count=1)
                rows = [[_element(c) for c in _split_top(row, re.compile("&"))]
                        for row in _split_top(body, _ROW_BREAK_RE)]
                rows = [r for r in rows if any(r)]
                if rows:
                    found.append((start, rows))
    return [rows for _, rows in sorted(found, key=lambda f: f[0])]


def _bag(grid: list[list[str]]) -> Counter:
    return Counter(e for row in grid for e in row if e)


def _lines(grid: list[list[str]], by_column: bool) -> list[Counter]:
    width = max(len(r) for r in grid)
    padded = [r + [""] * (width - len(r)) for r in grid]
    lines = list(zip(*padded)) if by_column else padded
    return [Counter(e for e in line if e) for line in lines]


def _size(grid: list[list[str]]) -> tuple[int, int]:
    return len(grid), max((max((c + 1 for c, e in enumerate(row) if e), default=0) for row in grid), default=0)


def compute_matrix_metrics(output: str, expected: str) -> MatrixMetrics:
    wanted, found = matrices(expected), matrices(output)
    metrics = MatrixMetrics(expected=len(wanted), elements=sum(sum(_bag(g).values()) for g in wanted))
    candidates = sorted((-_dice(_bag(e), _bag(o)), abs(i - j), i, j)
                        for i, e in enumerate(wanted) for j, o in enumerate(found))
    used_i, used_j = set(), set()
    for neg, _, i, j in candidates:
        if -neg < _MIN_PAIR_SIMILARITY:
            break
        if i in used_i or j in used_j:
            continue
        used_i.add(i)
        used_j.add(j)
        exp, out = wanted[i], found[j]
        rows = _align(_lines(exp, False), _lines(out, False))
        cols = _align(_lines(exp, True), _lines(out, True))
        metrics.paired += 1
        metrics.other_size += _size(exp) != _size(out)
        matched = set()
        for r, row in enumerate(exp):
            for c, element in enumerate(row):
                rr, cc = rows.get(r), cols.get(c)
                if element and rr is not None and cc is not None and cc < len(out[rr]) and out[rr][cc] == element:
                    metrics.in_place += 1
                    matched.add((rr, cc))
        metrics.extra += sum(1 for r, row in enumerate(out) for c, e in enumerate(row) if e and (r, c) not in matched)
    metrics.extra += sum(sum(_bag(g).values()) for j, g in enumerate(found) if j not in used_j)
    return metrics
