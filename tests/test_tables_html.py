"""HTML tables to GFM with flattened header paths (OmniDocBench ground truth conversion)."""

from parserx.tables.html import html_table_to_markdown


def test_html_table_to_markdown_handles_rowspan_colspan_and_multirow_headers():
    html = """
    <table>
      <thead>
        <tr>
          <th rowspan="2">项目</th>
          <th colspan="2">2025</th>
        </tr>
        <tr>
          <th>Q1</th>
          <th>Q2</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td rowspan="2">收入</td>
          <td>10</td>
          <td>12</td>
        </tr>
        <tr>
          <td>11</td>
          <td>13</td>
        </tr>
      </tbody>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 项目 | 2025 > Q1 | Q2 |",
        "| --- | --- | --- |",
        "| 收入 | 10 | 12 |",
        "|  | 11 | 13 |",
    ])


def test_html_table_to_markdown_uses_first_row_as_header_when_th_missing():
    html = """
    <table>
      <tr><td>型号</td><td>功率</td></tr>
      <tr><td>KFAW-1-80型</td><td>80kW</td></tr>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 型号 | 功率 |",
        "| --- | --- |",
        "| KFAW-1-80型 | 80kW |",
    ])


def test_html_table_to_markdown_preserves_inline_content_order():
    html = """
    <table>
      <tr><th>说明</th></tr>
      <tr><td>主图<img src="chart.png" alt="图表"/><br><div>第二行</div></td></tr>
    </table>
    """

    markdown = html_table_to_markdown(html)

    assert markdown == "\n".join([
        "| 说明 |",
        "| --- |",
        "| 主图 ![图表](chart.png) / 第二行 |",
    ])
