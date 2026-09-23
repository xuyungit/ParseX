"""Table structure metric (metric version 2.0, guide §9.2 item 1).

Both sides are parsed into TableGrid (GFM or HTML).  Tables are paired by
cell-content similarity; unpaired tables stay in the denominators, so a
missing table lowers recall and an extra one lowers precision.  Within a pair,
rows and columns are aligned by dynamic programming (as in GriTS), so an
inserted or dropped row only costs that row instead of shifting every cell.
A cell counts as correct when an output cell sits at the aligned position with
the same rowspan, colspan and normalized content.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from parserx.eval.normalize import normalize_cell, normalize_label
from parserx.tables import Cell, TableGrid, find_tables

# Pairs with less shared content than this are treated as different tables
# (one missing, one extra) rather than as a badly recognised match.
_MIN_PAIR_SIMILARITY = 0.2


@dataclass
class TableMetrics:
    detected_count: int = 0
    expected_count: int = 0
    matched_count: int = 0
    missing_tables: int = 0
    extra_tables: int = 0
    cell_precision: float | None = None
    cell_recall: float | None = None
    cell_f1: float | None = None  # None: no tables on either side
    header_association: float | None = None  # None: no expected data cells under a header
    merged_cell_accuracy: float | None = None  # None: no spanning cells expected
    column_accuracy: float | None = None  # fraction of pairs with equal column counts
    pairs: list[tuple[int, int]] = field(default_factory=list)


def compute_table_metrics(output_md: str, expected_md: str) -> TableMetrics:
    detected = [_grid_or_empty(span.grid) for span in find_tables(output_md)]
    expected = [_grid_or_empty(span.grid) for span in find_tables(expected_md)]
    if not detected and not expected:
        return TableMetrics()

    pairs = _pair_tables(expected, detected)
    total_out = sum(_count_cells(g) for g in detected)
    total_exp = sum(_count_cells(g) for g in expected)

    correct = header_ok = merged_ok = cols_ok = 0
    for e_idx, o_idx in pairs:
        stats = _compare_pair(expected[e_idx], detected[o_idx])
        correct += stats.correct
        header_ok += stats.header_ok
        merged_ok += stats.merged_ok
        cols_ok += expected[e_idx].n_cols == detected[o_idx].n_cols
    # Unpaired expected tables stay in these denominators too.
    header_total = sum(len(_data_cells(g)) for g in expected if g.header_rows)
    merged_total = sum(len(_spanning_cells(g)) for g in expected)

    precision = correct / total_out if total_out else 0.0
    recall = correct / total_exp if total_exp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return TableMetrics(
        detected_count=len(detected),
        expected_count=len(expected),
        matched_count=len(pairs),
        missing_tables=len(expected) - len(pairs),
        extra_tables=len(detected) - len(pairs),
        cell_precision=round(precision, 4),
        cell_recall=round(recall, 4),
        cell_f1=round(f1, 4),
        header_association=round(header_ok / header_total, 4) if header_total else None,
        merged_cell_accuracy=round(merged_ok / merged_total, 4) if merged_total else None,
        column_accuracy=round(cols_ok / len(pairs), 4) if pairs else None,
        pairs=pairs,
    )


# ── Pairing ─────────────────────────────────────────────────────────────


def _grid_or_empty(grid: TableGrid | None) -> TableGrid:
    return grid if grid is not None else TableGrid(n_rows=0, n_cols=0)


def _nonempty(grid: TableGrid) -> list[Cell]:
    return [cell for cell in grid.cells if normalize_cell(cell.content)]


def _count_cells(grid: TableGrid) -> int:
    return len(_nonempty(grid))


def _dice(a: Counter, b: Counter) -> float:
    total = sum(a.values()) + sum(b.values())
    return 2 * sum((a & b).values()) / total if total else 0.0


def _pair_tables(expected: list[TableGrid], detected: list[TableGrid]) -> list[tuple[int, int]]:
    bags_e = [Counter(normalize_cell(c.content) for c in _nonempty(g)) for g in expected]
    bags_o = [Counter(normalize_cell(c.content) for c in _nonempty(g)) for g in detected]
    candidates = sorted(
        (
            (-_dice(be, bo), abs(i - j), i, j)
            for i, be in enumerate(bags_e)
            for j, bo in enumerate(bags_o)
        ),
    )
    used_e: set[int] = set()
    used_o: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for neg_sim, _, i, j in candidates:
        if -neg_sim < _MIN_PAIR_SIMILARITY:
            break
        if i in used_e or j in used_o:
            continue
        used_e.add(i)
        used_o.add(j)
        pairs.append((i, j))
    return sorted(pairs)


# ── Alignment within a pair ─────────────────────────────────────────────


@dataclass
class _PairStats:
    correct: int
    header_ok: int
    merged_ok: int


def _line_bags(matrix: list[list[Cell | None]], by_column: bool) -> list[Counter]:
    lines = list(zip(*matrix)) if by_column and matrix else matrix
    return [
        Counter(normalize_cell(c.content) for c in line if c is not None and normalize_cell(c.content))
        for line in lines
    ]


def _align(a: list[Counter], b: list[Counter]) -> dict[int, int]:
    """Monotonic alignment maximizing total similarity (weighted LCS)."""
    n, m = len(a), len(b)
    sim = [[_dice(a[i], b[j]) for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = score[i + 1][j + 1] + sim[i][j] if sim[i][j] > 0 else -1.0
            score[i][j] = max(take, score[i + 1][j], score[i][j + 1])
    mapping: dict[int, int] = {}
    i = j = 0
    while i < n and j < m:
        if sim[i][j] > 0 and score[i][j] == score[i + 1][j + 1] + sim[i][j]:
            mapping[i] = j
            i += 1
            j += 1
        elif score[i][j] == score[i + 1][j]:
            i += 1
        else:
            j += 1
    return mapping


def _header_paths(grid: TableGrid, matrix: list[list[Cell | None]]) -> list[str]:
    paths: list[str] = []
    for col in range(grid.n_cols):
        parts: list[str] = []
        for row in range(grid.header_rows):
            cell = matrix[row][col]
            text = normalize_label(cell.content) if cell is not None else ""
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        paths.append("".join(parts))
    return paths


def _data_cells(grid: TableGrid) -> list[Cell]:
    return [c for c in _nonempty(grid) if c.row >= grid.header_rows]


def _spanning_cells(grid: TableGrid) -> list[Cell]:
    return [c for c in _nonempty(grid) if c.rowspan > 1 or c.colspan > 1]


def _compare_pair(expected: TableGrid, detected: TableGrid) -> _PairStats:
    exp_matrix = expected.slot_matrix()
    out_matrix = detected.slot_matrix()
    row_map = _align(_line_bags(exp_matrix, False), _line_bags(out_matrix, False))
    col_map = _align(_line_bags(exp_matrix, True), _line_bags(out_matrix, True))
    origins = {(c.row, c.col): c for c in detected.cells}

    def counterpart(cell: Cell) -> Cell | None:
        r, c = row_map.get(cell.row), col_map.get(cell.col)
        if r is None or c is None:
            return None
        other = origins.get((r, c))
        if other is None or normalize_cell(other.content) != normalize_cell(cell.content):
            return None
        return other

    correct = merged_ok = 0
    for cell in _nonempty(expected):
        other = counterpart(cell)
        if other is not None and (other.rowspan, other.colspan) == (cell.rowspan, cell.colspan):
            correct += 1
            if cell.rowspan > 1 or cell.colspan > 1:
                merged_ok += 1

    header_ok = 0
    if expected.header_rows:
        exp_paths = _header_paths(expected, exp_matrix)
        out_paths = _header_paths(detected, out_matrix)
        for cell in _data_cells(expected):
            other = counterpart(cell)
            if other is not None and exp_paths[cell.col] == out_paths[other.col]:
                header_ok += 1

    return _PairStats(correct=correct, header_ok=header_ok, merged_ok=merged_ok)
