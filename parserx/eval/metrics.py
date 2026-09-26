"""Evaluation metrics for document parsing quality (metric version 2.0).

Report order (guide §9.3): hard checks → table structure → char_f1 and edit
distance → reading order → headings → key-content errors → real requests →
time → cost.  Text-side normalization lives in ``parserx.eval.normalize``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

from rapidfuzz.distance import LCSseq, Levenshtein

from parserx.eval.key_content import KeyContentMetrics, compute_key_content_errors
from parserx.eval.normalize import canonicalize, char_sequence
from parserx.eval.order import OrderMetrics, compute_order_metrics
from parserx.eval.tables import TableMetrics, compute_table_metrics
from parserx.eval.text import compute_edit_distance, normalize_for_comparison

# Bump whenever a metric definition changes; results with different versions
# are never compared against each other.
METRIC_VERSION = "2.2"  # 2.1 (2026-09-24, Q28): merged cells against annotations without spans
# 2.2 (2026-09-26, Q68): headings — lines inside fenced code blocks are not headings; titles compared after NFKC

__all__ = [
    "METRIC_VERSION",
    "CostMetrics",
    "EvalResult",
    "HeadingMetrics",
    "KeyContentMetrics",
    "OrderMetrics",
    "TableMetrics",
    "TextMetrics",
    "compute_edit_distance",
    "compute_heading_metrics",
    "compute_key_content_errors",
    "compute_order_metrics",
    "compute_residual_diagnostics",
    "compute_table_metrics",
    "compute_text_metrics",
    "evaluate_markdown",
    "fmt_metric",
    "mean_defined",
]


@dataclass
class TextMetrics:
    """Order-aware text quality on canonicalized text (see parserx.eval.normalize)."""

    edit_distance: float = 0.0  # exact Levenshtein / max length (0 = identical)
    char_precision: float = 0.0  # LCS / output length
    char_recall: float = 0.0  # LCS / expected length
    char_f1: float = 0.0
    char_bag_f1: float = 0.0  # order-blind character-frequency F1 (diagnostic only)
    images_expected: int = 0  # image placeholders removed before scoring
    images_output: int = 0


@dataclass
class HeadingMetrics:
    """Heading detection quality metrics."""

    precision: float = 0.0  # What fraction of detected headings are correct
    recall: float = 0.0  # What fraction of ground truth headings were detected
    f1: float | None = 0.0  # None: no headings on either side
    detected_count: int = 0
    expected_count: int = 0
    correct_count: int = 0


@dataclass
class CostMetrics:
    """Processing cost metrics.  Call counts are real requests recorded at the service boundary."""

    wall_time_seconds: float = 0.0
    ocr_calls: int = 0
    vlm_calls: int = 0
    llm_calls: int = 0
    ocr_pages: int = 0  # pages submitted to OCR (one batch request can carry many)
    attempts: dict[str, int] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None  # token usage is recorded from Phase 1 on
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
    order: OrderMetrics = field(default_factory=OrderMetrics)
    key_content: KeyContentMetrics = field(default_factory=KeyContentMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)
    warnings: list[str] = field(default_factory=list)
    residuals: ResidualDiagnostics = field(default_factory=ResidualDiagnostics)


# ── Text metrics ────────────────────────────────────────────────────────


def compute_text_metrics(output: str, expected: str) -> TextMetrics:
    """Order-aware character metrics on canonicalized text."""
    out_canon = canonicalize(output)
    exp_canon = canonicalize(expected)
    out_seq = char_sequence(out_canon.text)
    exp_seq = char_sequence(exp_canon.text)
    images = {"images_expected": exp_canon.image_count, "images_output": out_canon.image_count}

    if not out_seq and not exp_seq:
        return TextMetrics(char_precision=1.0, char_recall=1.0, char_f1=1.0, char_bag_f1=1.0, **images)
    if not out_seq or not exp_seq:
        return TextMetrics(edit_distance=1.0, **images)

    lcs = LCSseq.similarity(out_seq, exp_seq)
    common = sum((Counter(out_seq) & Counter(exp_seq)).values())
    return TextMetrics(
        edit_distance=round(Levenshtein.distance(out_seq, exp_seq) / max(len(out_seq), len(exp_seq)), 4),
        char_precision=round(lcs / len(out_seq), 4),
        char_recall=round(lcs / len(exp_seq), 4),
        char_f1=round(_f1(lcs / len(out_seq), lcs / len(exp_seq)), 4),
        char_bag_f1=round(_f1(common / len(out_seq), common / len(exp_seq)), 4),
        **images,
    )


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


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
    """Extract (level, title) pairs from markdown heading lines (not inside fenced code blocks, Q68)."""
    headings = []
    fence: str | None = None
    for line in markdown.splitlines():
        stripped = line.strip()
        opening = re.match(r"^(`{3,}|~{3,})", stripped)
        if opening:
            if fence is None:
                fence = opening.group(1)[0] * len(opening.group(1))
            elif stripped.startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append((level, title))
    return headings


def _normalize_heading(text: str) -> str:
    """Normalize heading text for fuzzy matching (NFKC first: full-width "９．" is "9.", Q68)."""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
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
        # Nothing to find: not applicable when nothing was emitted either.
        return HeadingMetrics(
            f1=None if not detected else 0.0,
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
        if "[图片]" in extra_text or "![" in extra_text:
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


# ── Whole-document evaluation ───────────────────────────────────────────


def evaluate_markdown(
    output_md: str,
    expected_md: str,
    *,
    name: str = "",
    cost: CostMetrics | None = None,
    warnings: list[str] | None = None,
) -> EvalResult:
    """All metrics for one document; the single entry point for eval, compare and tool-eval."""
    return EvalResult(
        document_name=name,
        text=compute_text_metrics(output_md, expected_md),
        headings=compute_heading_metrics(output_md, expected_md),
        tables=compute_table_metrics(output_md, expected_md),
        order=compute_order_metrics(output_md, expected_md),
        key_content=compute_key_content_errors(output_md, expected_md),
        cost=cost or CostMetrics(),
        warnings=list(warnings or []),
        residuals=compute_residual_diagnostics(output_md, expected_md),
    )


def mean_defined(values: Iterable[float | None]) -> float | None:
    """Mean over the values that are defined; None when none are."""
    defined = [v for v in values if v is not None]
    return sum(defined) / len(defined) if defined else None


def fmt_metric(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"
