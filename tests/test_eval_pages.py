"""Per-page scoring (vision-first E0): a Markdown document is split by the page each block comes from."""

from parserx.eval.pages import markdown_blocks, split_pages

_PAGES = [
    "第一页的正文讲述桥梁的基本情况。第一页还有一段关于荷载的说明文字。",
    "第二页给出有限元模型的建立过程。表 1 列出材料参数。",
    "第三页比较计算结果并总结全文的主要结论。",
]


def test_blocks_go_to_the_page_they_come_from():
    markdown = ("# 引言\n\n第一页的正文讲述桥梁的基本情况。\n\n第二页给出有限元模型的建立过程。\n\n"
                "第三页比较计算结果并总结全文的主要结论。\n")
    pages = split_pages(markdown, _PAGES)
    assert "第一页的正文" in pages[0] and "第二页给出" in pages[1] and "第三页比较" in pages[2]
    assert "# 引言" in pages[0]  # a block found nowhere stays with the block after it at the start


def test_a_block_found_nowhere_stays_with_the_block_before_it():
    markdown = "第二页给出有限元模型的建立过程。\n\n$$x = y$$\n\n第三页比较计算结果并总结全文的主要结论。"
    pages = split_pages(markdown, _PAGES)
    assert "$$x = y$$" in pages[1]


def test_a_table_running_over_pages_is_split_by_its_rows():
    pages = ["表 1 材料参数\n材料 弹性模量 泊松比\n钢材 206 GPa 0.30", "混凝土 34.5 GPa 0.20\n第二页的正文从这里开始。"]
    table = ("| 材料 | 弹性模量 | 泊松比 |\n|---|---|---|\n| 钢材 | 206 GPa | 0.30 |\n"
             "| 混凝土 | 34.5 GPa | 0.20 |\n")
    split = split_pages(table, pages)
    assert "钢材" in split[0] and "混凝土" in split[1]


def test_notation_does_not_move_a_block():
    pages = ["由式可得 αᵢ + β² = γᵢⱼ，其中各量均为无量纲的系数。", "另一页上完全不同的一段正文内容。"]
    split = split_pages("由式可得 $\\alpha_{i} + \\beta^{2} = \\gamma_{ij}$，其中各量均为无量纲的系数。", pages)
    assert split[0] and not split[1]


def test_tables_code_and_display_math_stay_whole():
    markdown = "<table>\n<tr><td>1</td></tr>\n\n<tr><td>2</td></tr>\n</table>\n\n```\na\n\nb\n```\n\n$$\nx\n\ny\n$$"
    assert len(markdown_blocks(markdown)) == 3
