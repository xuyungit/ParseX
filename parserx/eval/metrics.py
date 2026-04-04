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
    cost: CostMetrics = field(default_factory=CostMetrics)


# ── Edit distance ───────────────────────────────────────────────────────


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
    """
    # Normalize whitespace for fair comparison
    out_norm = _normalize_for_comparison(output)
    exp_norm = _normalize_for_comparison(expected)

    if not exp_norm and not out_norm:
        return 0.0
    if not exp_norm or not out_norm:
        return 1.0

    # For very long texts, sample to keep computation reasonable
    max_len = 10000
    if len(out_norm) > max_len or len(exp_norm) > max_len:
        out_norm = out_norm[:max_len]
        exp_norm = exp_norm[:max_len]

    distance = _levenshtein(out_norm, exp_norm)
    max_possible = max(len(out_norm), len(exp_norm))
    return distance / max_possible


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
    normalized text matches (ignoring whitespace and punctuation).
    """
    detected = _extract_headings(output_md)
    expected = _extract_headings(expected_md)

    if not expected:
        return HeadingMetrics(
            detected_count=len(detected),
            expected_count=0,
        )

    # Match detected to expected using normalized text
    expected_norms = [_normalize_heading(t) for _, t in expected]
    matched = set()
    correct = 0

    for _, title in detected:
        norm = _normalize_heading(title)
        for i, exp_norm in enumerate(expected_norms):
            if i in matched:
                continue
            # Exact match or substring match (heading text might be truncated)
            if norm == exp_norm or norm in exp_norm or exp_norm in norm:
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
