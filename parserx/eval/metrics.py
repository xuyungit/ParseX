"""Evaluation metrics for document parsing quality.

Metrics:
- Text: normalized edit distance, character-level precision/recall
- Headings: precision, recall, F1 of detected headings
- Cost: API call counts, processing time
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from parserx.text_utils import compute_edit_distance, normalize_for_comparison


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
    warning_count: int = 0
    llm_fallback_hits: int = 0
    pages_processed: int = 0
    images_total: int = 0
    images_skipped: int = 0


@dataclass
class ResidualSnippet:
    """Representative residual snippet for a document diff."""

    text: str = ""
    line_count: int = 0
    char_count: int = 0


@dataclass
class ResidualDiagnostics:
    """High-level residual themes plus representative diff snippets."""

    themes: list[str] = field(default_factory=list)
    extra: ResidualSnippet = field(default_factory=ResidualSnippet)
    missing: ResidualSnippet = field(default_factory=ResidualSnippet)
    extra_block_count: int = 0
    missing_block_count: int = 0


@dataclass
class EvalResult:
    """Complete evaluation result for a single document."""

    document_name: str = ""
    text: TextMetrics = field(default_factory=TextMetrics)
    headings: HeadingMetrics = field(default_factory=HeadingMetrics)
    tables: TableMetrics = field(default_factory=TableMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)
    warnings: list[str] = field(default_factory=list)
    residuals: ResidualDiagnostics = field(default_factory=ResidualDiagnostics)


# ── Edit distance ───────────────────────────────────────────────────────


# ── Text metrics ────────────────────────────────────────────────────────


def compute_text_metrics(output: str, expected: str) -> TextMetrics:
    """Compute text quality metrics by comparing output to expected."""
    edit_dist = compute_edit_distance(output, expected)

    # Character-level precision/recall using set intersection
    out_chars = set(enumerate(output))  # (position, char) pairs won't work for set comparison
    # Use character frequency comparison instead
    out_norm = normalize_for_comparison(output)
    exp_norm = normalize_for_comparison(expected)

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


def compute_residual_diagnostics(output: str, expected: str) -> ResidualDiagnostics:
    """Summarize the largest extra/missing content regions and likely themes."""
    out_lines_raw = _meaningful_lines(output)
    exp_lines_raw = _meaningful_lines(expected)
    out_norm = [normalize_for_comparison(line) for line in out_lines_raw]
    exp_norm = [normalize_for_comparison(line) for line in exp_lines_raw]

    matcher = SequenceMatcher(a=exp_norm, b=out_norm, autojunk=False)
    extra_blocks: list[ResidualSnippet] = []
    missing_blocks: list[ResidualSnippet] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            snippet = _build_residual_snippet(exp_lines_raw[i1:i2])
            if snippet.char_count:
                missing_blocks.append(snippet)
        if tag in {"replace", "insert"}:
            snippet = _build_residual_snippet(out_lines_raw[j1:j2])
            if snippet.char_count:
                extra_blocks.append(snippet)

    extra = _pick_largest_snippet(extra_blocks)
    missing = _pick_largest_snippet(missing_blocks)
    themes = _infer_residual_themes(output, expected, extra, missing)

    return ResidualDiagnostics(
        themes=themes,
        extra=extra,
        missing=missing,
        extra_block_count=len(extra_blocks),
        missing_block_count=len(missing_blocks),
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
    text = text.replace("—", "-").replace("–", "-").replace("－", "-")
    text = text.replace("：", ":").replace("，", ",")
    text = re.sub(r"^[—–\-一]{2,}", "--", text)
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


def _meaningful_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for raw in markdown.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("<!-- PAGE "):
            continue
        lines.append(stripped)
    return lines


def _build_residual_snippet(lines: list[str], max_chars: int = 280) -> ResidualSnippet:
    if not lines:
        return ResidualSnippet()
    text = "\n".join(lines)
    compact = text if len(text) <= max_chars else text[: max_chars - 1] + "…"
    return ResidualSnippet(
        text=compact,
        line_count=len(lines),
        char_count=len(normalize_for_comparison(text)),
    )


def _pick_largest_snippet(snippets: list[ResidualSnippet]) -> ResidualSnippet:
    if not snippets:
        return ResidualSnippet()
    return max(snippets, key=lambda item: (item.char_count, item.line_count))


def _infer_residual_themes(
    output: str,
    expected: str,
    extra: ResidualSnippet,
    missing: ResidualSnippet,
) -> list[str]:
    themes: list[str] = []
    out_len = len(normalize_for_comparison(output))
    exp_len = len(normalize_for_comparison(expected))

    if out_len > exp_len * 1.08:
        themes.append("output_heavy")
    elif exp_len > out_len * 1.08:
        themes.append("output_light")

    extra_text = extra.text
    missing_text = missing.text

    if extra_text:
        if "Text content preserved in OCR body text." in extra_text or "[图片]" in extra_text or "![" in extra_text:
            themes.append("image_reference_markup")
        if extra_text.count("|") >= 4:
            themes.append("table_markup_shape")
        if any(line.startswith("#") for line in extra_text.splitlines()):
            themes.append("extra_heading")
    if missing_text:
        if missing_text.count("|") >= 4:
            themes.append("missing_table_shape")
        if any(line.startswith("#") for line in missing_text.splitlines()):
            themes.append("missing_heading")

    deduped: list[str] = []
    for theme in themes:
        if theme not in deduped:
            deduped.append(theme)
    return deduped


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
