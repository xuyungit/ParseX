"""Arithmetic consistency of recognized tables (plan P2-7, round 1 finding G1) on synthetic grids."""

from parserx.tables import Cell, TableGrid
from parserx.tables.arithmetic import arithmetic_issues


def _grid(rows, header_rows=1):
    return TableGrid(n_rows=len(rows), n_cols=len(rows[0]), header_rows=header_rows,
                     cells=[Cell(row=r, col=c, content=v, is_header=r < header_rows)
                            for r, row in enumerate(rows) for c, v in enumerate(row)])


PRICES = [["名称", "数量", "单价", "金额"],
          ["板式支座", "48", "1,250.00", "60,000.00"],
          ["盆式支座", "16", "8,300.50", "132,808.00"],
          ["球型支座", "8", "12,000", "96,000.00"],
          ["伸缩缝", "120", "35.5", "4,260.00"]]


def test_a_row_that_breaks_the_columns_product_is_reported():
    rows = [r[:] for r in PRICES]
    rows[3][3] = "86,000.00"  # a misread digit
    issues = arithmetic_issues(_grid(rows))
    assert len(issues) == 1 and "row 3" in issues[0] and "96000" in issues[0].replace(",", "")


def test_consistent_tables_and_unrelated_numbers_are_quiet():
    assert arithmetic_issues(_grid(PRICES)) == []
    years = [["年份", "产量", "人数"], ["2021", "350", "12"], ["2022", "410", "15"], ["2023", "388", "14"]]
    assert arithmetic_issues(_grid(years)) == []


def test_a_total_row_confirmed_by_other_columns_reports_the_column_that_disagrees():
    rows = [["项目", "数量", "重量", "金额"], ["甲", "10", "1.5", "100.00"], ["乙", "20", "3.0", "250.00"],
            ["丙", "5", "0.5", "50.00"], ["合计", "35", "5.0", "420.00"]]  # 100 + 250 + 50 = 400
    rows_ok = [r[:] for r in rows]
    rows_ok[4][3] = "400.00"
    assert arithmetic_issues(_grid(rows_ok)) == []
    issues = arithmetic_issues(_grid(rows))
    assert len(issues) == 1 and "row 4" in issues[0] and "400" in issues[0]
    # one column alone agreeing with a sum is not enough to call a row a total
    single = [["项目", "数量"], ["甲", "10"], ["乙", "20"], ["丙", "30"]]
    assert arithmetic_issues(_grid(single)) == []


def test_a_column_of_ones_is_not_a_product_relation():
    rows = [["名称", "件数", "金额", "金额"], ["甲", "1", "30", "30"], ["乙", "1", "45", "45"], ["丙", "1", "20", "21"]]
    assert arithmetic_issues(_grid(rows)) == []


def test_rows_with_a_factor_of_one_do_not_prove_a_product():
    # mostly single items: the "product" is only the price copied — rows with 10 items do not break anything
    rows = [["名称", "数量", "单价", "不含税单价"]] + [[f"品{i}", "1", f"{100 + i}.50", f"{100 + i}.50"] for i in range(6)]
    rows += [["盆式支座", "10", "6787.61", "6787.61"], ["球型支座", "10", "8051.33", "8051.33"]]
    assert arithmetic_issues(_grid(rows)) == []


def test_an_increasing_key_column_is_not_a_sum():
    # bar diameters 6, 8, 10: areas and weights of 6 and 8 add up to those of 10 (6² + 8² = 10²) by coincidence
    rows = [["直径", "面积", "重量"], ["6", "28.27", "0.222"], ["8", "50.27", "0.395"], ["10", "78.54", "0.617"],
            ["12", "113.1", "0.888"]]
    assert arithmetic_issues(_grid(rows)) == []
