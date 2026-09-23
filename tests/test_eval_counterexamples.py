"""The five minimal counterexamples from the 2026-09-23 audit (guide §9.2).

Phase 0 exit criterion: each one must be caught by a metric or a hard check.
"""

from parserx.eval.gate import evaluate_gate, run_record
from parserx.eval.metrics import compute_table_metrics, compute_text_metrics

_TWO_ROWS = "| 名称 | 数值 |\n|---|---|\n| 甲 | 10 |\n| 乙 | 20 |\n"


def test_swapped_table_values_lower_table_f1():
    swapped = "| 名称 | 数值 |\n|---|---|\n| 甲 | 20 |\n| 乙 | 10 |\n"
    metrics = compute_table_metrics(swapped, _TWO_ROWS)
    assert metrics.cell_f1 < 1.0


def test_missing_second_table_counts_in_f1_and_missing_tables():
    other = "| 项目 | 单位 |\n|---|---|\n| 长度 | 米 |\n"
    metrics = compute_table_metrics(_TWO_ROWS, _TWO_ROWS + "\n正文\n\n" + other)
    assert metrics.cell_f1 < 1.0
    assert metrics.missing_tables == 1


def test_html_output_of_same_table_is_detected_and_matches():
    html = (
        "<table><tr><th>名称</th><th>数值</th></tr>"
        "<tr><td>甲</td><td>10</td></tr><tr><td>乙</td><td>20</td></tr></table>"
    )
    metrics = compute_table_metrics(html, _TWO_ROWS)
    assert metrics.detected_count == 1
    assert metrics.cell_f1 == 1.0


def test_swapped_subjects_lower_char_f1():
    expected = "甲公司向乙公司支付货款一百万元。"
    output = "乙公司向甲公司支付货款一百万元。"
    metrics = compute_text_metrics(output, expected)
    assert metrics.char_f1 < 1.0
    assert metrics.char_bag_f1 == 1.0  # the old, order-blind measure could not see it


def test_all_documents_failing_is_a_hard_failure():
    record = run_record([], failed=[("a", "boom"), ("b", "boom")], not_executed=[])
    outcome = evaluate_gate(record, baseline=None)
    assert outcome.exit_code == 2
    assert outcome.hard_failures
