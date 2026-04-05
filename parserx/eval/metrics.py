"""Evaluation metrics for document parsing quality.

Metrics:
- Text: normalized edit distance, character-level precision/recall
- Headings: precision, recall, F1 of detected headings
- Cost: API call counts, processing time
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


@dataclass
class TextMetrics:
    """Text extraction quality metrics."""

    edit_distance: float = 0.0  # Normalized edit distance (0 = identical, 1 = completely different)
    char_precision: float = 0.0  # What fraction of output chars are in ground truth
    char_recall: float = 0.0  # What fraction of ground truth chars are in output
    char_f1: float = 0.0


@dataclass
class HeadingMetrics:
    """Heading detection quality metrics."""

    precision: float = 0.0  # What fraction of detected headings are correct
    recall: float = 0.0  # What fraction of ground truth headings were detected
    f1: float = 0.0
    detected_count: int = 0
    expected_count: int = 0
    correct_count: int = 0


@dataclass
class TableMetrics:
    """Table extraction quality metrics."""

    detected_count: int = 0
    expected_count: int = 0
    matched_count: int = 0  # Tables matched by column count
    cell_precision: float = 0.0  # What fraction of output cells are correct
    cell_recall: float = 0.0  # What fraction of expected cells were found
    cell_f1: float = 0.0
    column_accuracy: float = 0.0  # Fraction of tables with correct column count


@dataclass
class CostMetrics:
    """Processing cost metrics."""

    wall_time_seconds: float = 0.0
    ocr_calls: int = 0
    vlm_calls: int = 0
    llm_calls: int = 0
    pages_processed: int = 0
    images_total: int = 0
    images_skipped: int = 0


@dataclass
class EvalResult:
    """Complete evaluation result for a single document."""

    document_name: str = ""
    text: TextMetrics = field(default_factory=TextMetrics)
    headings: HeadingMetrics = field(default_factory=HeadingMetrics)
    tables: TableMetrics = field(default_factory=TableMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)


# ── Edit distance ───────────────────────────────────────────────────────


_CHUNK_SIZE = 5000  # Max chars per Levenshtein call (keeps O(n²) tractable)


def _chunked_edit_distance(a: str, b: str) -> float:
    """Split *a* and *b* into aligned chunks and average their distances.

    Both strings are divided into the same number of chunks (proportional
    to their lengths), so the tail of a longer document is always compared
    rather than silently dropped.
    """
    n_chunks = max(
        (max(len(a), len(b)) + _CHUNK_SIZE - 1) // _CHUNK_SIZE,
        1,
    )
    a_step = max(len(a) // n_chunks, 1)
    b_step = max(len(b) // n_chunks, 1)

    total_dist = 0
    total_max = 0
    for i in range(n_chunks):
        a_chunk = a[i * a_step : (i + 1) * a_step] if i < n_chunks - 1 else a[i * a_step :]
        b_chunk = b[i * b_step : (i + 1) * b_step] if i < n_chunks - 1 else b[i * b_step :]
        if not a_chunk and not b_chunk:
            continue
        d = _levenshtein(a_chunk, b_chunk)
        total_dist += d
        total_max += max(len(a_chunk), len(b_chunk))

    return total_dist / max(total_max, 1)


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,       # insert
                prev_row[j + 1] + 1,   # delete
                prev_row[j] + cost,    # replace
            ))
        prev_row = curr_row

    return prev_row[-1]


def compute_edit_distance(output: str, expected: str) -> float:
    """Normalized edit distance between output and expected text.

    Returns 0.0 for identical, 1.0 for completely different.
    Uses character-level comparison after whitespace normalization.

    For texts exceeding ``_CHUNK_SIZE`` characters, the strings are split
    into equal-length chunks and the per-chunk normalized distances are
    averaged.  This avoids the O(n²) cost of a single Levenshtein call on
    very long strings while still capturing regressions in **every** part
    of the document (not just the first 10 000 characters).
    """
    # Normalize whitespace for fair comparison
    out_norm = _normalize_for_comparison(output)
    exp_norm = _normalize_for_comparison(expected)

    if not exp_norm and not out_norm:
        return 0.0
    if not exp_norm or not out_norm:
        return 1.0

    # Short texts — compute directly
    if len(out_norm) <= _CHUNK_SIZE and len(exp_norm) <= _CHUNK_SIZE:
        distance = _levenshtein(out_norm, exp_norm)
        return distance / max(len(out_norm), len(exp_norm))

    # Long texts — chunked comparison to keep cost manageable
    # while still covering the entire document.
    return _chunked_edit_distance(out_norm, exp_norm)


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for fair comparison: collapse whitespace, strip markup."""
    # Remove markdown headings markers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove page markers
    text = re.sub(r"<!-- PAGE \d+ -->", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Text metrics ────────────────────────────────────────────────────────


def compute_text_metrics(output: str, expected: str) -> TextMetrics:
    """Compute text quality metrics by comparing output to expected."""
    edit_dist = compute_edit_distance(output, expected)

    # Character-level precision/recall using set intersection
    out_chars = set(enumerate(output))  # (position, char) pairs won't work for set comparison
    # Use character frequency comparison instead
    out_norm = _normalize_for_comparison(output)
    exp_norm = _normalize_for_comparison(expected)

    if not exp_norm and not out_norm:
        return TextMetrics(edit_distance=0.0, char_precision=1.0, char_recall=1.0, char_f1=1.0)

    # Count character overlap
    from collections import Counter
    out_counter = Counter(out_norm)
    exp_counter = Counter(exp_norm)

    common = sum((out_counter & exp_counter).values())
    precision = common / max(sum(out_counter.values()), 1)
    recall = common / max(sum(exp_counter.values()), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)

    return TextMetrics(
        edit_distance=edit_dist,
        char_precision=round(precision, 4),
        char_recall=round(recall, 4),
        char_f1=round(f1, 4),
    )


# ── Heading metrics ─────────────────────────────────────────────────────


def _extract_headings(markdown: str) -> list[tuple[int, str]]:
    """Extract (level, title) pairs from markdown heading lines."""
    headings = []
    for line in markdown.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append((level, title))
    return headings


def _normalize_heading(text: str) -> str:
    """Normalize heading text for fuzzy matching."""
    text = re.sub(r"\s+", "", text)
    text = text.replace("：", ":").replace("，", ",")
    return text.lower()


def compute_heading_metrics(
    output_md: str, expected_md: str
) -> HeadingMetrics:
    """Compare detected headings against ground truth headings.

    Uses fuzzy matching: headings are considered correct if their
    normalized text matches (ignoring whitespace and punctuation)
    **and** their heading level is identical.  This ensures that a
    ``### Section`` is not silently accepted where ``## Section`` was
    expected — level mismatches indicate structural regressions.
    """
    detected = _extract_headings(output_md)
    expected = _extract_headings(expected_md)

    if not expected:
        return HeadingMetrics(
            detected_count=len(detected),
            expected_count=0,
        )

    # Match detected to expected using (level, normalized text)
    expected_entries = [(lvl, _normalize_heading(t)) for lvl, t in expected]
    matched = set()
    correct = 0

    for det_level, title in detected:
        norm = _normalize_heading(title)
        for i, (exp_level, exp_norm) in enumerate(expected_entries):
            if i in matched:
                continue
            # Text must match (exact or substring) AND level must match
            text_ok = norm == exp_norm or norm in exp_norm or exp_norm in norm
            if text_ok and det_level == exp_level:
                matched.add(i)
                correct += 1
                break

    precision = correct / max(len(detected), 1)
    recall = correct / max(len(expected), 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)

    return HeadingMetrics(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        detected_count=len(detected),
        expected_count=len(expected),
        correct_count=correct,
    )


# ── Table metrics ──────────────────────────────────────────────────────


def _extract_tables(markdown: str) -> list[list[list[str]]]:
    """Extract tables from markdown as list of 2D grids.

    Each table is a list of rows, each row a list of cell strings.
    Skips the separator row (|---|---|).
    """
    tables: list[list[list[str]]] = []
    current_table: list[list[str]] = []
    in_table = False

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            # Skip separator rows
            if re.match(r"^\|[\s\-:|]+(\|[\s\-:|]+)+\|$", stripped):
                continue
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            current_table.append(cells)
            in_table = True
        else:
            if in_table and current_table:
                tables.append(current_table)
                current_table = []
            in_table = False

    if current_table:
        tables.append(current_table)

    return tables


def _normalize_cell(text: str) -> str:
    """Normalize cell text for comparison."""
    return re.sub(r"\s+", "", text).lower()


def _table_cells_to_set(tables: list[list[list[str]]]) -> set[str]:
    """Convert all table cells to a set of (table_idx, row, col, normalized_text) keys."""
    cells = set()
    for t_idx, table in enumerate(tables):
        for r_idx, row in enumerate(table):
            for c_idx, cell in enumerate(row):
                norm = _normalize_cell(cell)
                if norm:  # Skip empty cells
                    cells.add(f"{t_idx}:{r_idx}:{c_idx}:{norm}")
    return cells


def compute_table_metrics(
    output_md: str, expected_md: str,
) -> TableMetrics:
    """Compare extracted tables against ground truth tables.

    Matches tables by order (first output table vs first expected table, etc.)
    then computes cell-level precision/recall/F1.

    Also checks column count accuracy as a structural metric.
    """
    detected_tables = _extract_tables(output_md)
    expected_tables = _extract_tables(expected_md)

    if not expected_tables and not detected_tables:
        return TableMetrics()

    if not expected_tables:
        return TableMetrics(detected_count=len(detected_tables))

    if not detected_tables:
        return TableMetrics(expected_count=len(expected_tables))

    # Match tables by order, compute per-matched-pair cell overlap
    n_match = min(len(detected_tables), len(expected_tables))
    col_correct = 0
    total_out_cells = 0
    total_exp_cells = 0
    total_common = 0

    for i in range(n_match):
        det = detected_tables[i]
        exp = expected_tables[i]

        # Column count check
        det_cols = max((len(r) for r in det), default=0)
        exp_cols = max((len(r) for r in exp), default=0)
        if det_cols == exp_cols:
            col_correct += 1

        # Cell-level comparison using normalized text multiset
        from collections import Counter
        det_cells = Counter(
            _normalize_cell(c) for row in det for c in row if _normalize_cell(c)
        )
        exp_cells = Counter(
            _normalize_cell(c) for row in exp for c in row if _normalize_cell(c)
        )

        common = sum((det_cells & exp_cells).values())
        total_common += common
        total_out_cells += sum(det_cells.values())
        total_exp_cells += sum(exp_cells.values())

    precision = total_common / max(total_out_cells, 1)
    recall = total_common / max(total_exp_cells, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-10)

    return TableMetrics(
        detected_count=len(detected_tables),
        expected_count=len(expected_tables),
        matched_count=n_match,
        cell_precision=round(precision, 4),
        cell_recall=round(recall, 4),
        cell_f1=round(f1, 4),
        column_accuracy=round(col_correct / max(n_match, 1), 4),
    )
