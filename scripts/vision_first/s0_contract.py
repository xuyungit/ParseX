"""Scanned-page contract (docs/v2_vision_first_scanned.md §2): complete allocation of the scan engine's blocks.

The same answer as on a native page (``p0_contract``: ``blocks`` of ``parts``, ``aside``, ``unresolved``; the same
checks, repair and rendering), with the scan engine's blocks in place of the text layer's lines: ``K1``, ``K2`` … in
the engine's order.  ``copy`` places an engine block's reading as it is; ``write`` writes from the image the blocks
it replaces (or, with none, content the engine did not read, in a region); ``table`` takes an engine table's grid,
``cell`` rewrites one of its cells; every engine block exactly once, in the body or in ``aside`` (the ledger's
``ocr_block`` items).  Display formulas are written or copied here, not asked apart: the engine's LaTeX is itself a
block.  The page record (``s0_inputs``) holds the engine blocks as its ``lines`` (``unit`` ``K``), the tables as
``tables`` (``T1`` … with the block that holds each), and the local reading as ``local``.
"""

from __future__ import annotations

import json

import p0_contract as contract

SAMPLE = {
    "blocks": [
        {"id": "B1", "type": "title", "level": 2, "region": None,
         "parts": [{"kind": "copy", "lines": ["K2"], "text": "", "engine": [], "cell": None}]},
        {"id": "B2", "type": "text", "level": None, "region": None, "parts": [
            {"kind": "write", "lines": ["K3"], "text": "其中 $x_{i}^{2}$ 为第 $i$ 个测点的位移，共 12 个测点。", "engine": [], "cell": None}]},
        {"id": "B3", "type": "formula", "level": None, "region": None,
         "parts": [{"kind": "copy", "lines": ["K4"], "text": "", "engine": [], "cell": None}]},
        {"id": "B4", "type": "figure", "level": None, "region": [120, 800, 1100, 1250],
         "parts": [{"kind": "copy", "lines": ["K5"], "text": "", "engine": [], "cell": None}]},
        {"id": "B5", "type": "caption", "level": None, "region": None,
         "parts": [{"kind": "copy", "lines": ["K6"], "text": "", "engine": [], "cell": None}]},
        {"id": "B6", "type": "table", "level": None, "region": None,
         "parts": [{"kind": "table", "lines": ["K7"], "text": "", "engine": [], "cell": None},
                   {"kind": "cell", "lines": [], "text": "−14.61", "engine": [], "cell": "T1:4:3"}]},
        {"id": "B7", "type": "text", "level": None, "region": [100, 1600, 1150, 1650],
         "parts": [{"kind": "write", "lines": [], "text": "注：引擎漏读的一行。", "engine": [], "cell": None}]},
    ],
    "aside": [{"lines": ["K1"], "reason": "页眉", "to": "excluded"},
              {"lines": ["K8"], "reason": "页码", "to": "excluded"}],
    "unresolved": [],
}

INSTRUCTIONS = """你在整理一页扫描件，写成供大模型阅读的 Markdown 的块序列。像人整理文档一样：看页面图确定这页有哪些块、阅读顺序、每块是什么。扫描引擎已经把这页读成了一个个块：读对的就照抄（引用块号，由程序放入引擎的读数），读错的、漏读的才照图自己写。

给你的材料：
1. 页面图（宽 {width}、高 {height} 像素）。下面所有的框都是这张图的像素坐标 [x0, y0, x1, y1]。
2. 扫描引擎的块：引擎读出的这页内容，逐块编号（K1、K2……，按引擎给的顺序，不一定是阅读顺序）。每块给出框 box、引擎的类型 label（如 text 正文、paragraph_title 标题、table 表格、display_formula 行间公式、image 图、header 页眉、footer 页脚、number 页码）和读数 text（表格见【引擎的表格】，公式是 LaTeX）。引擎的字大多是对的，也会认错字、漏字漏行、建错表格的行列、看错类型。
3. 本地识读的行：另一个识别工具独立读出的文字行，带框。只作参考，它也会认错，公式、表格和小字尤其不可靠。
4. 版面检测的区域：只作提示，可能有错。

输出一个 JSON 对象：
- blocks：按阅读顺序排列的块。一块是一个段落：表单的各个字段、地址的各行、列表项这类在页面上各自成行的内容，每行各成一块。每块有 id（B1、B2……）、type（title 标题、text 正文段落、list 列表项、formula 行间公式、table 表格、figure 图、caption 图表标题或图注、footnote 脚注、other 其他）、level（标题按版面估计的层级 1–6，不是标题填 null）、region（图，以及引擎没有读到的内容，给出它在页面图上的框；其他填 null）、parts（块的内容，按顺序相接）。
- parts 的每一项有 kind、lines、text、engine、cell（engine 一律填空列表；只有 cell 部分填位置，其他部分填 null）：
  - copy：lines 是要照抄的引擎块（"K3"，或区间 "K3-K9" 表示 K3 到 K9 的每一块），按阅读顺序排列；text 填空串。程序放入这些块的读数，同一块里的读数按换行接起来。只照抄与图一致的块；一个引擎块里混了两段内容，照抄不了，就用 write。
  - write：text 是你照图写的内容，lines 是它替换的引擎块。引擎块里有认错的字、漏掉的字或行、与图不符的上下标，就用 write 照图把这一块整块写出：被替换的引擎块里的内容都要写进去，不能丢。图上有而引擎没有读到的内容，lines 为空列表，并给块填 region。行内公式写成 $…$；行间公式单独成块，type 为 formula，text 只写公式的 LaTeX。照图写，不总结、不翻译、不补全，也不改正原文的错字：原件印错的字照印的写；看不清的地方写 〔?〕 并记入 unresolved，不要猜。
  - table：lines 是一个表格引擎块，表格就是【引擎的表格】里的那张。
  - cell：引擎的表格里有几格与图不一致（认错的字、与图不符的上下标），就在这张表所在的块里，对每一格加一个 cell：cell 写这一格的位置 "T1:4:3"（表 T1 第 4 行第 3 列，按【引擎的表格】里 rows 的行列数，从 1 数起），text 是照图写出的这一格的全部内容，lines 填空列表。只改有问题的格子，其余照抄。表格本身建错了（行列错位、漏行、合并格不对），才改用 write 照图写出整张表（HTML），lines 仍是这个表格引擎块。
  - 图：type 为 figure，region 是图的框；引擎的图块（label 为 image、chart 等）用 copy 放进这个块，图里的文字块也放进这个块或列入 aside。
- aside：不放进正文的引擎块。每项给 lines、reason（页眉、页脚、页码、图内文字等）、to（去处：excluded 表示不输出；或接收它的块 id）。只有各页重复出现的页眉、页脚、页码、装饰用 excluded；首页的刊名、卷期、日期、编号等只出现一次的信息是正文内容，要输出。
- unresolved：看不清、定不下的地方，每项给 where（块号或块 id）与 what。

硬性要求：每个引擎块恰好出现一次——在某个 copy、write 或 table 的 lines 里，或在 aside 的 lines 里。引用的引擎块、块 id 都必须存在。只输出 JSON。

JSON 样例（只示意格式，内容与本页无关）：
{sample}"""


def prompt(page: dict) -> str:
    image = page["image"]
    return INSTRUCTIONS.format(width=image["width"], height=image["height"],
                               sample=json.dumps(SAMPLE, ensure_ascii=False))


def context(page: dict) -> str:
    """The page's data, as text: the engine blocks, the engine's tables, the local reading, the detector regions."""
    table_of = {t["block"]: f"T{k}" for k, t in enumerate(page.get("tables") or [], 1)}
    parts = ["【扫描引擎的块】（每块一个 JSON）"]
    for unit in page["lines"]:
        shown = {"id": unit["id"], "label": unit["label"], "box": unit["box"]}
        if unit["id"] in table_of:
            shown["table"] = table_of[unit["id"]]
        else:
            shown["text"] = unit["shown"]
        parts.append(json.dumps(shown, ensure_ascii=False))
    if not page["lines"]:
        parts.append("（引擎没有读出任何块）")
    if page.get("tables"):
        parts.append("\n【引擎的表格】（每张一个 JSON；rows 是逐行的格子，null 是被上方或左方合并格占住的位置）")
        parts += [json.dumps({"id": f"T{k}", "block": t["block"], "rows": contract.grid_rows(t["html"])},
                             ensure_ascii=False) for k, t in enumerate(page["tables"], 1)]
    parts.append("\n【本地识读的行】（每行一个 JSON）")
    parts += [json.dumps(line, ensure_ascii=False) for line in page.get("local") or []] or ["（没有读出文字）"]
    parts.append("\n【版面检测区域】")
    parts += [json.dumps(r, ensure_ascii=False) for r in page.get("regions", [])]
    return "\n".join(parts)
