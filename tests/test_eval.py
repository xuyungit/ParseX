"""Tests for evaluation metrics."""

from parserx.eval.metrics import (
    compute_edit_distance,
    compute_heading_metrics,
    compute_text_metrics,
)


# ── Edit distance ───────────────────────────────────────────────────────


def test_edit_distance_identical():
    assert compute_edit_distance("hello world", "hello world") == 0.0


def test_edit_distance_completely_different():
    dist = compute_edit_distance("abc", "xyz")
    assert dist == 1.0


def test_edit_distance_partial():
    dist = compute_edit_distance("hello", "hallo")
    assert 0.0 < dist < 1.0


def test_edit_distance_empty():
    assert compute_edit_distance("", "") == 0.0
    assert compute_edit_distance("hello", "") == 1.0
    assert compute_edit_distance("", "hello") == 1.0


def test_edit_distance_ignores_markup():
    """Page markers and heading prefixes should not affect distance."""
    a = "<!-- PAGE 1 -->\n# Title\n\nContent here."
    b = "Title\n\nContent here."
    dist = compute_edit_distance(a, b)
    assert dist < 0.1  # Very close after normalization


# ── Text metrics ────────────────────────────────────────────────────────


def test_text_metrics_identical():
    m = compute_text_metrics("hello world", "hello world")
    assert m.edit_distance == 0.0
    assert m.char_f1 > 0.99


def test_text_metrics_partial():
    m = compute_text_metrics("hello world foo", "hello world bar")
    assert m.char_f1 > 0.5  # Significant overlap


# ── Heading metrics ─────────────────────────────────────────────────────


def test_heading_metrics_perfect():
    output = "# Title\n## Section 1\n## Section 2"
    expected = "# Title\n## Section 1\n## Section 2"
    m = compute_heading_metrics(output, expected)
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1 == 1.0


def test_heading_metrics_missing():
    output = "# Title"
    expected = "# Title\n## Section 1\n## Section 2"
    m = compute_heading_metrics(output, expected)
    assert m.precision == 1.0  # All detected are correct
    assert m.recall < 1.0  # But missed some
    assert m.detected_count == 1
    assert m.expected_count == 3


def test_heading_metrics_extra():
    output = "# Title\n## Section 1\n## Extra\n## Section 2"
    expected = "# Title\n## Section 1\n## Section 2"
    m = compute_heading_metrics(output, expected)
    assert m.recall == 1.0  # All expected found
    assert m.precision < 1.0  # But has an extra


def test_heading_metrics_chinese():
    output = "# 第一章 总则\n## 一、基本原则"
    expected = "# 第一章 总则\n## 一、基本原则"
    m = compute_heading_metrics(output, expected)
    assert m.f1 == 1.0


def test_heading_metrics_no_expected():
    output = "# Title\n## Section"
    expected = "No headings here."
    m = compute_heading_metrics(output, expected)
    assert m.expected_count == 0
    assert m.detected_count == 2
