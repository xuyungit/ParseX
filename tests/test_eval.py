"""Tests for evaluation metrics."""

import logging

from parserx.config.schema import ParserXConfig, apply_overrides
from parserx.eval.compare import CompareRow, compare_results, format_compare_report
from parserx.eval.runner import EvalRunner
from parserx.eval.metrics import (
    CostMetrics,
    EvalResult,
    TableMetrics,
    _extract_tables,
    _normalize_cell,
    compute_edit_distance,
    compute_heading_metrics,
    compute_table_metrics,
    compute_text_metrics,
)
from parserx.eval.reporting import build_config_report_metadata
from parserx.eval.warnings import categorize_warning, summarize_warning_types


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


def test_edit_distance_long_text_identical():
    """Long identical texts should still produce 0.0."""
    a = "x" * 20000
    b = "x" * 20000
    assert compute_edit_distance(a, b) == 0.0


def test_edit_distance_long_text_suffix_regression():
    """A different suffix beyond 10k chars must NOT be silently ignored."""
    shared = "x" * 10000
    a = shared + "a" * 2000
    b = shared + "b" * 2000
    dist = compute_edit_distance(a, b)
    # The 2k suffix is completely different — distance should be noticeable
    assert dist > 0.1


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


def test_heading_metrics_level_mismatch():
    """### Section vs ## Section should NOT count as a correct match."""
    output = "# Title\n### Section 1"
    expected = "# Title\n## Section 1"
    m = compute_heading_metrics(output, expected)
    # "Title" matches (both H1), but "Section 1" is H3 vs H2 — mismatch
    assert m.correct_count == 1
    assert m.recall < 1.0
    assert m.precision < 1.0


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


def test_apply_overrides_supports_dotted_paths():
    config = apply_overrides(
        ParserXConfig(),
        [
            "builders.ocr.engine=none",
            "processors.chapter.llm_fallback=false",
            "services.llm.model=test-model",
        ],
    )

    assert config.builders.ocr.engine == "none"
    assert config.processors.chapter.llm_fallback is False
    assert config.services.llm.model == "test-model"


def test_format_report_includes_warning_and_api_sections():
    config = apply_overrides(
        ParserXConfig(),
        [
            "services.vlm.model=demo-vlm",
            "services.llm.model=demo-llm",
        ],
    )
    results = [
        EvalResult(
            document_name="doc-a",
            cost=CostMetrics(
                wall_time_seconds=1.2,
                ocr_calls=1,
                vlm_calls=0,
                llm_calls=2,
                warning_count=3,
                llm_fallback_hits=4,
            ),
            warnings=["warning 1", "warning 2", "warning 3"],
        ),
    ]

    report = EvalRunner.format_report(
        results,
        metadata=build_config_report_metadata(
            config,
            overrides=["services.vlm.model=demo-vlm", "services.llm.model=demo-llm"],
        ),
    )

    assert "Run Metadata" in report
    assert "VLM service" in report
    assert "model=demo-vlm" in report
    assert "Overrides" in report
    assert "Total warnings: 3" in report
    assert "API calls (OCR/VLM/LLM): 1/0/2" in report
    assert "LLM fallback hits: 4" in report
    assert "Warning Types" in report
    assert "Warning Hotspots" in report


def test_warning_categorization_groups_known_patterns():
    counts = summarize_warning_types([
        "Page 1: low-confidence VLM description (number mismatch).",
        "Page 2: image output missing rendered reference.",
        "Rendered text volume drifted beyond tolerance: source=10 chars, output=20 chars.",
    ])

    assert categorize_warning("Page 1: low-confidence VLM description (number mismatch).") == "number_mismatch"
    assert counts["number_mismatch"] == 1
    assert counts["image_missing_reference"] == 1
    assert counts["text_volume_drift"] == 1


def test_compare_results_aligns_by_document_name():
    results_a = [EvalResult(document_name="doc-b"), EvalResult(document_name="doc-a")]
    results_b = [EvalResult(document_name="doc-a"), EvalResult(document_name="doc-b")]

    rows = compare_results(results_a, results_b)

    assert [row.document_name for row in rows] == ["doc-a", "doc-b"]


def test_compare_results_warns_on_non_overlapping_documents(caplog):
    caplog.set_level(logging.WARNING)

    rows = compare_results(
        [EvalResult(document_name="doc-a"), EvalResult(document_name="doc-b")],
        [EvalResult(document_name="doc-b"), EvalResult(document_name="doc-c")],
    )

    assert [row.document_name for row in rows] == ["doc-b"]
    assert "only present in compare A" in caplog.text
    assert "only present in compare B" in caplog.text


def test_evaluate_dir_accepts_single_document_directory(tmp_path, monkeypatch):
    doc_dir = tmp_path / "doc-a"
    doc_dir.mkdir()
    (doc_dir / "expected.md").write_text("# Title\n", encoding="utf-8")
    (doc_dir / "input.pdf").write_bytes(b"%PDF-1.4 fake")

    runner = EvalRunner(ParserXConfig())
    monkeypatch.setattr(
        runner,
        "evaluate_single",
        lambda input_path, expected_md_path, name="": EvalResult(document_name=name or input_path.stem),
    )

    results = runner.evaluate_dir(doc_dir)

    assert [result.document_name for result in results] == ["doc-a"]


def test_evaluate_dir_filters_to_include_set(tmp_path, monkeypatch):
    doc_a = tmp_path / "doc-a"
    doc_a.mkdir()
    (doc_a / "expected.md").write_text("# A\n", encoding="utf-8")
    (doc_a / "input.pdf").write_bytes(b"%PDF-1.4 fake")

    doc_b = tmp_path / "doc-b"
    doc_b.mkdir()
    (doc_b / "expected.md").write_text("# B\n", encoding="utf-8")
    (doc_b / "input.pdf").write_bytes(b"%PDF-1.4 fake")

    runner = EvalRunner(ParserXConfig())
    monkeypatch.setattr(
        runner,
        "evaluate_single",
        lambda input_path, expected_md_path, name="": EvalResult(document_name=name or input_path.stem),
    )

    results = runner.evaluate_dir(tmp_path, include_docs={"doc-b"})

    assert [result.document_name for result in results] == ["doc-b"]


def test_format_compare_report_shows_deltas():
    rows = [
        CompareRow(
            document_name="doc-a",
            result_a=EvalResult(
                document_name="doc-a",
                cost=CostMetrics(wall_time_seconds=2.0, warning_count=2, llm_calls=1),
            ),
            result_b=EvalResult(
                document_name="doc-a",
                cost=CostMetrics(wall_time_seconds=1.5, warning_count=1, llm_calls=0),
            ),
        )
    ]
    rows[0].result_a.text.char_f1 = 0.8
    rows[0].result_b.text.char_f1 = 0.9
    rows[0].result_a.text.edit_distance = 0.3
    rows[0].result_b.text.edit_distance = 0.2
    rows[0].result_a.headings.f1 = 0.7
    rows[0].result_b.headings.f1 = 0.8
    rows[0].result_a.tables.cell_f1 = 0.6
    rows[0].result_b.tables.cell_f1 = 0.65
    rows[0].result_a.warnings = [
        "Page 1: low-confidence VLM description (number mismatch).",
        "Page 1: image output missing rendered reference.",
    ]
    rows[0].result_b.warnings = [
        "Page 1: image output missing rendered reference.",
    ]

    report = format_compare_report(
        rows,
        label_a="base",
        label_b="exp",
        metadata_a=[("VLM service", "provider=openai | model=base-vlm")],
        metadata_b=[("VLM service", "provider=openai | model=exp-vlm")],
    )

    assert "Comparing **base** vs **exp**" in report
    assert "base Metadata" in report
    assert "exp Metadata" in report
    assert "model=base-vlm" in report
    assert "model=exp-vlm" in report
    assert "| Char F1 | 0.800 | 0.900 | +0.100 | higher |" in report
    assert "Warning Type Delta" in report
    assert "| Number mismatch | 1 | 0 | -1 |" in report
    assert "| doc-a | -0.100 | +0.100 | +0.100 | +0.050 | -1 | -1 | -0.5s |" in report
