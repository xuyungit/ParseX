"""Must-read checks (eval/checks.py): characters compared, not layout."""

from parserx.eval.checks import Check, run_checks


def test_a_check_passes_whatever_the_markup_and_spacing():
    md = "> **报告编号：** ZJA1-X001\n| 资产总计 | 1 234.５6 |"
    results = run_checks(md, [Check(id="a", text="报告编号：ZJA1-X001"), Check(id="b", text="1234.56")])
    assert [r.passed for r in results] == [True, True]


def test_a_check_counts_occurrences_and_an_absent_one_fails_when_found():
    md = "华通公司 与 华通公司；华南公司"
    results = run_checks(md, [Check(id="a", text="华通公司", min=3), Check(id="x", text="华南公司", absent=True)])
    assert [(r.found, r.passed) for r in results] == [(2, False), (1, False)]
