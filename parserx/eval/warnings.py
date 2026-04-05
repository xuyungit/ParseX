"""Warning categorization helpers for evaluation reporting."""

from __future__ import annotations

import re
from collections import Counter

_WARNING_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"number mismatch", re.IGNORECASE), "number_mismatch", "Number mismatch"),
    (re.compile(r"low-confidence VLM description", re.IGNORECASE), "low_confidence_vlm", "Low-confidence VLM"),
    (re.compile(r"image output missing rendered reference", re.IGNORECASE), "image_missing_reference", "Image missing reference"),
    (re.compile(r"Rendered text volume drifted beyond tolerance", re.IGNORECASE), "text_volume_drift", "Text volume drift"),
    (re.compile(r"Table count mismatch", re.IGNORECASE), "table_count_mismatch", "Table count mismatch"),
    (re.compile(r"Page marker mismatch", re.IGNORECASE), "page_marker_mismatch", "Page marker mismatch"),
    (re.compile(r"jump from H\d+ to H\d+", re.IGNORECASE), "heading_level_jump", "Heading level jump"),
    (re.compile(r"orphan H\d+", re.IGNORECASE), "orphan_heading", "Orphan heading"),
    (re.compile(r"empty heading", re.IGNORECASE), "empty_heading", "Empty heading"),
    (re.compile(r"Chapter file is empty", re.IGNORECASE), "empty_chapter_file", "Empty chapter file"),
]

_WARNING_LABELS = {code: label for _, code, label in _WARNING_RULES}
_UNKNOWN_WARNING_CODE = "other"
_WARNING_LABELS[_UNKNOWN_WARNING_CODE] = "Other"


def categorize_warning(warning: str) -> str:
    """Map a raw warning string to a stable warning category code."""
    for pattern, code, _label in _WARNING_RULES:
        if pattern.search(warning):
            return code
    return _UNKNOWN_WARNING_CODE


def warning_label(code: str) -> str:
    """Get display label for a warning category code."""
    return _WARNING_LABELS.get(code, code.replace("_", " ").title())


def summarize_warning_types(warnings: list[str]) -> dict[str, int]:
    """Count warning categories for a flat list of warnings."""
    counts = Counter(categorize_warning(warning) for warning in warnings)
    return dict(
        sorted(
            counts.items(),
            key=lambda item: (-item[1], warning_label(item[0]), item[0]),
        )
    )
