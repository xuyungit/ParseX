"""Tests for evaluation metrics."""

from parserx.eval.metrics import (
    TableMetrics,
    _extract_tables,
    _normalize_cell,
    compute_edit_distance,
    compute_heading_metrics,
    compute_table_metrics,
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


def test_edit_distance_long_text_truncation():
    """Very long texts get truncated to 10000 chars, should still work."""
    a = "x" * 20000
    b = "x" * 20000
    assert compute_edit_distance(a, b) == 0.0


# ── Text metrics ────────────────────────────────────────────────────────


def test_text_metrics_identical():
    m = compute_text_metrics("hello world", "hello world")
    assert m.edit_distance == 0.0
    assert m.char_f1 > 0.99


def test_text_metrics_partial():
    m = compute_text_metrics("hello world foo", "hello world bar")
    assert m.char_f1 > 0.5  # Significant overlap


def test_text_metrics_empty():
    m = compute_text_metrics("", "")
    assert m.char_f1 == 1.0
    assert m.edit_distance == 0.0


def test_text_metrics_one_empty():
    m = compute_text_metrics("some text", "")
    assert m.char_recall == 0.0


def test_text_metrics_cjk():
    """Chinese text comparison."""
    m = compute_text_metrics("你好世界测试文档", "你好世界测试文档")
    assert m.edit_distance == 0.0
    assert m.char_f1 > 0.99


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


def test_heading_metrics_fuzzy_match():
    """Heading with extra whitespace or punctuation should still match."""
    output = "## 一、 基本原则"
    expected = "## 一、基本原则"
    m = compute_heading_metrics(output, expected)
    assert m.f1 == 1.0


def test_heading_metrics_both_empty():
    m = compute_heading_metrics("no headings", "also no headings")
    assert m.expected_count == 0
    assert m.detected_count == 0


# ── Table metrics ──────────────────────────────────────────────────────


def test_extract_tables_single():
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    tables = _extract_tables(md)
    assert len(tables) == 1
    assert tables[0] == [["A", "B"], ["1", "2"], ["3", "4"]]


def test_extract_tables_multiple():
    md = (
        "Some text\n\n"
        "| X | Y |\n|---|---|\n| a | b |\n\n"
        "More text\n\n"
        "| P | Q | R |\n|---|---|---|\n| 1 | 2 | 3 |"
    )
    tables = _extract_tables(md)
    assert len(tables) == 2
    assert len(tables[0][0]) == 2  # 2 columns
    assert len(tables[1][0]) == 3  # 3 columns


def test_extract_tables_none():
    assert _extract_tables("No tables here") == []


def test_extract_tables_skip_separator():
    """Separator rows should not appear as data."""
    md = "| H1 | H2 |\n|---|---|\n| d1 | d2 |"
    tables = _extract_tables(md)
    assert len(tables) == 1
    # Should have header + 1 data row, no separator
    assert len(tables[0]) == 2
    assert tables[0][0] == ["H1", "H2"]
    assert tables[0][1] == ["d1", "d2"]


def test_normalize_cell():
    assert _normalize_cell("  Hello World  ") == "helloworld"
    assert _normalize_cell("王 艳 辉") == "王艳辉"
    assert _normalize_cell("") == ""


def test_table_metrics_perfect():
    md = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
    m = compute_table_metrics(md, md)
    assert m.cell_f1 == 1.0
    assert m.column_accuracy == 1.0
    assert m.detected_count == 1
    assert m.expected_count == 1


def test_table_metrics_no_tables():
    m = compute_table_metrics("no tables", "no tables")
    assert m.detected_count == 0
    assert m.expected_count == 0
    assert m.cell_f1 == 0.0


def test_table_metrics_missing_table():
    expected = "| A | B |\n|---|---|\n| 1 | 2 |"
    m = compute_table_metrics("no tables", expected)
    assert m.detected_count == 0
    assert m.expected_count == 1


def test_table_metrics_extra_table():
    output = "| A | B |\n|---|---|\n| 1 | 2 |"
    m = compute_table_metrics(output, "no tables")
    assert m.detected_count == 1
    assert m.expected_count == 0


def test_table_metrics_partial_match():
    output = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Charlie | 35 |"
    expected = "| Name | Age |\n|---|---|\n| Alice | 30 |\n| Bob | 25 |"
    m = compute_table_metrics(output, expected)
    # "Name", "Age", "Alice", "30" are common; "Charlie","35" vs "Bob","25" differ
    assert 0.0 < m.cell_f1 < 1.0
    assert m.cell_precision > 0.5
    assert m.cell_recall > 0.5
    assert m.column_accuracy == 1.0


def test_table_metrics_column_mismatch():
    output = "| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |"
    expected = "| A | B |\n|---|---|\n| 1 | 2 |"
    m = compute_table_metrics(output, expected)
    assert m.column_accuracy == 0.0  # Column count differs


def test_table_metrics_chinese_cells():
    output = "| 序号 | 姓名 |\n|---|---|\n| 1 | 王艳辉 |"
    expected = "| 序号 | 姓 名 |\n|---|---|\n| 1 | 王艳辉 |"
    m = compute_table_metrics(output, expected)
    # "姓名" normalized equals "姓名" (spaces removed)
    assert m.cell_f1 == 1.0


def test_table_metrics_multiple_tables():
    output = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "text\n\n"
        "| X | Y |\n|---|---|\n| a | b |"
    )
    expected = (
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "text\n\n"
        "| X | Y |\n|---|---|\n| a | b |"
    )
    m = compute_table_metrics(output, expected)
    assert m.cell_f1 == 1.0
    assert m.detected_count == 2
    assert m.expected_count == 2
    assert m.matched_count == 2
