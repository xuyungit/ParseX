"""P0 output contract (execution plan §3.2): complete allocation of the text layer, checked by the program.

The service model answers one JSON object: ``blocks`` in reading order, each made of ``parts`` —

- ``copy``: lines of the text layer (``L3`` or ``L3-L9``), placed by the program as they are, with the script
  candidates of their size and baseline (contract v5: the model sees them as each line's ``copy_as``, and writes the
  line when the image disagrees);
- ``write``: text the model writes, with the lines it replaces (none for content no text line holds: then the block
  names its ``region`` on the page image) and the engine entries it drew on; a display formula's text is left empty,
  its LaTeX asked apart (``v_formulas``);
- ``table``: the lines of a table, whose grid the native extraction builds (shown to the model as ``T1``, ``T2`` …);
- ``cell``: one cell of such a grid written from the image (contract v6): its address (``T1:3:2``, table, row, column
  as shown), the cell's whole text, no lines — the table part keeps them; the rest of the grid stays copied;

— and ``aside``, lines not put in the body, each with a reason and where they end up (``excluded`` or the block
that takes them, e.g. a figure for its in-image text); ``unresolved`` lists what the model could not settle.

Three checks (Q139): the answer parses, it fits the schema, and it keeps the invariants — every line of the text
layer exactly once, every referenced line, entry and block exists, LaTeX balanced, no unmapped code point written.
The failures are told back once (``feedback``); what still breaks an invariant is repaired mechanically
(``repair``: unknown references dropped, a repeated line kept at its first place, an unallocated line copied back at
its place by line order), every repair listed.  ``render`` turns an allocation into the page's Markdown.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

from parserx.content.text import join_wrapped

BLOCK_TYPES = ("title", "text", "list", "formula", "table", "figure", "caption", "footnote", "other")
PART_KINDS = ("copy", "write", "table", "cell")
EXCLUDED = "excluded"
CONTRACT_VERSION = 7  # 1: P0 (508693c); 2: scripts on copy/table, write holds only its lines (393e560); 3: variables as $…$ (Q142); 4: a block is one paragraph (V); 5: no scripts switch — copied lines take their candidates (``copy_as``) — and display formulas get their LaTeX from a request of their own (Q143); 6: the program's table grids shown, a cell written from the image by its address (``cell`` parts); 7: typos of the original kept as printed (instructions only; user 2026-09-30)
UNREADABLE = "〔?〕"  # the visible mark of a glyph no one could read (execution plan §1: never deleted, never guessed)

_NULLABLE = lambda schema: {"anyOf": [schema, {"type": "null"}]}  # noqa: E731
_STRINGS = {"type": "array", "items": {"type": "string"}}
PART_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["kind", "lines", "text", "engine", "cell"],
    "properties": {"kind": {"type": "string", "enum": list(PART_KINDS)}, "lines": _STRINGS, "text": {"type": "string"},
                   "engine": _STRINGS, "cell": _NULLABLE({"type": "string"})},
}
BLOCK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["id", "type", "level", "region", "parts"],
    "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": list(BLOCK_TYPES)},
                   "level": _NULLABLE({"type": "integer"}),
                   "region": _NULLABLE({"type": "array", "items": {"type": "number"}}),
                   "parts": {"type": "array", "items": PART_SCHEMA}},
}
SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["blocks", "aside", "unresolved"],
    "properties": {
        "blocks": {"type": "array", "items": BLOCK_SCHEMA},
        "aside": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["lines", "reason", "to"],
            "properties": {"lines": _STRINGS, "reason": {"type": "string"}, "to": {"type": "string"}}}},
        "unresolved": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["where", "what"],
            "properties": {"where": {"type": "string"}, "what": {"type": "string"}}}},
    },
}
_VALIDATOR = Draft202012Validator(SCHEMA)

SAMPLE = {
    "blocks": [
        {"id": "B1", "type": "title", "level": 2, "region": None,
         "parts": [{"kind": "copy", "lines": ["L5"], "text": "", "engine": [], "cell": None}]},
        {"id": "B2", "type": "text", "level": None, "region": None, "parts": [
            {"kind": "copy", "lines": ["L6-L9"], "text": "", "engine": [], "cell": None},
            {"kind": "write", "lines": ["L10", "L11"], "text": "其中 $x_{i}^{2}$ 为第 $i$ 个测点的位移，", "engine": ["E2"], "cell": None},
            {"kind": "copy", "lines": ["L12-L14"], "text": "", "engine": [], "cell": None}]},
        {"id": "B3", "type": "formula", "level": None, "region": None,
         "parts": [{"kind": "write", "lines": ["L15-L31"], "text": "", "engine": ["E3"], "cell": None}]},
        {"id": "B4", "type": "figure", "level": None, "region": [120, 800, 1100, 1250], "parts": []},
        {"id": "B5", "type": "caption", "level": None, "region": None,
         "parts": [{"kind": "copy", "lines": ["L40"], "text": "", "engine": [], "cell": None}]},
        {"id": "B6", "type": "table", "level": None, "region": None,
         "parts": [{"kind": "table", "lines": ["L41-L80"], "text": "", "engine": [], "cell": None},
                   {"kind": "cell", "lines": [], "text": "−14.61", "engine": [], "cell": "T1:4:3"}]},
    ],
    "aside": [{"lines": ["L1-L4"], "reason": "页眉", "to": "excluded"},
              {"lines": ["L32-L39"], "reason": "图内文字", "to": "B4"}],
    "unresolved": [],
}

INSTRUCTIONS = """你在整理文档的一页，写成供大模型阅读的 Markdown 的块序列。像人整理文档一样：看页面图确定这页有哪些块、阅读顺序、每块是什么；能从 PDF 文字层复制的就复制（引用行号，由程序放入原文，字符准确）；复制不了的才自己照图写。

给你的材料：
1. 页面图（宽 {width}、高 {height} 像素）。下面所有的框都是这张图的像素坐标 [x0, y0, x1, y1]。
2. 文字层：这页 PDF 里的文字，逐行编号（L1、L2……，按 PDF 自己的顺序，不一定是阅读顺序）。每行给出框、主字号 size、主字体 font、原文 text。字号、字体或基线与本行主体不同的片段另列在 runs（base 是片段基线相对本行主基线的偏移，单位点，正数偏下、负数偏上）；odd 列出文字层里映射不到可读字符的字形（私用区、U+FFFD、括号部件）；copy_as 是程序只按字号与基线推出的上下标写法（<sup>…</sup>、<sub>…</sub>），复制这一行时放入的就是它，可能有错，以图为准。{engine_note}
{regions_note}

输出一个 JSON 对象：
- blocks：按阅读顺序排列的块。一块是一个段落：只有同一段落里折行的行才放进同一块；表单的各个字段、地址的各行、菜单项、列表项这类在页面上各自成行的内容，每行各成一块。每块有 id（B1、B2……）、type（title 标题、text 正文段落、list 列表项、formula 行间公式、table 表格、figure 图、caption 图表标题或图注、footnote 脚注、other 其他）、level（标题按版面估计的层级 1–6，不是标题填 null）、region（图，以及没有文字行可引用的内容，给出它在页面图上的框；其他填 null）、parts（块的内容，按顺序相接）。
- parts 的每一项有 kind、lines、text、engine、cell（只有 cell 部分填位置，其他部分填 null）：
  - copy：lines 是要复制的行（"L3"，或区间 "L3-L9" 表示 L3 到 L9 的每一行），按阅读顺序排列；text 填空串。程序放入这些行的文字（有 copy_as 的行放入 copy_as），同一块里的行按换行接起来。copy_as 与图不一致（图上没有这个上下标，或上下标不同），或行里有斜体的变量或式子（x、b_i、3n−1 这类数学符号），就不要复制这一行，用 write 照图把整行写出。
  - write：text 是你照图写的内容，lines 是它替换的行，engine 是参考了的引擎条目（没有就填空列表）。text 只写 lines 里这些行的内容：这些行的内容都要写进去，不能丢；它前后已经 copy 的字不要再写一遍。只在复制不了时写：含变量、式子或与图不符的上下标的行整行重写（行内公式写成 $…$，式子的写法照图）；odd 里的字形、文字层与图不一致的字，照图写出；图上有而文字层没有的文字，lines 为空列表，并给块填 region。照图写，不总结、不翻译、不补全，也不改正原文的错字（原件印错的字照印的写，不按上下文或同一列的规律推断）；看不清的地方写 〔?〕 并记入 unresolved，不要猜——浅得读不出、模糊的文字不要写成内容。
  - 行间公式：块的 type 为 formula，parts 只有一个 write，lines 是这个公式的全部行（含公式编号），engine 是对应的引擎条目，text 填空串——公式的 LaTeX 由程序另请一次、单独写出。每个带编号的行间公式单独成一块；图上有而文字层没有的公式，lines 为空列表，并给块填 region。
  - table：lines 是整张表的行，表格由程序从文字层建出，就是【程序建出的表格】里的那张（格子里已按 copy_as 写出上下标）。
  - cell：程序建出的表格里有几格与图不一致（映射不到的字形、认错的字、与图不符的上下标），就在这张表所在的块里，对每一格加一个 cell：cell 写这一格的位置 "T1:4:3"（表 T1 第 4 行第 3 列，按【程序建出的表格】里 rows 的行列数，从 1 数起），text 是照图写出的这一格的全部内容，lines 填空列表，engine 照常。只改有问题的格子，其余照抄；其他 part 的 cell 填 null。表格本身建错了（行列错位、漏行、把别的内容并了进来），才改用 write 照图写出整张表（HTML），lines 仍是整张表的行。
- aside：不放进正文的行。每项给 lines、reason（页眉、页脚、页码、图内文字等）、to（去处：excluded 表示不输出；或接收它的块 id，例如图内文字给那张图的块）。只有各页重复出现的页眉、页脚、页码、装饰线用 excluded；首页的刊名、卷期、日期、文章编号、DOI 等只出现一次的信息是正文内容，要输出。
- unresolved：看不清、定不下的地方，每项给 where（行号或块 id）与 what。

硬性要求：文字层的每一行恰好出现一次——在某个 copy、write 或 table 的 lines 里，或在 aside 的 lines 里。引用的行号、引擎条目、块 id 都必须存在。只输出 JSON。

JSON 样例（只示意格式，内容与本页无关）：
{sample}"""

_ENGINE_NOTE = ("\n3. 扫描引擎的读数条目（E1、E2……）：另一个识别工具独立读出的含公式的片段，带框与 LaTeX；公式结构通常对，"
                "个别字符可能认错、可能漏掉编号，以图为准。")
_REGIONS_NOTE = "{n}. 版面检测的区域：只作提示，可能有错。"


def prompt(page: dict) -> str:
    """The instructions for *page* (its input record); the data go in ``context``."""
    if unit_of(page) == "K":  # a scanned page: the scan engine's blocks are the units (``s0_contract``)
        import s0_contract

        return s0_contract.prompt(page)
    image = page["image"]
    engine = bool(page.get("engine"))
    return INSTRUCTIONS.format(width=image["width"], height=image["height"],
                               engine_note=_ENGINE_NOTE if engine else "",
                               regions_note=_REGIONS_NOTE.format(n=4 if engine else 3),
                               sample=json.dumps(SAMPLE, ensure_ascii=False))


def context(page: dict) -> str:
    """The page's data, as text: the text layer, the engine entries, the detector regions."""
    if unit_of(page) == "K":
        import s0_contract

        return s0_contract.context(page)
    parts = ["【文字层】（每行一个 JSON）"]
    lines = page["lines"]
    parts += [json.dumps(_shown(line), ensure_ascii=False) for line in lines] if lines else ["（这页没有文字层）"]
    if page.get("tables"):
        parts.append("\n【程序建出的表格】（每张一个 JSON；rows 是逐行的格子，null 是被上方或左方合并格占住的位置）")
        parts += [json.dumps({"id": f"T{k}", "lines": compress(sorted(int(x[1:]) for ref in t["lines"] for x in _names(ref))),
                              "rows": grid_rows(_scripted_cells(t["html"], {x for ref in t["lines"] for x in _names(ref)}, page))},
                             ensure_ascii=False) for k, t in enumerate(page["tables"], 1)]
    if page.get("engine"):
        parts.append("\n【扫描引擎条目】（每条一个 JSON）")
        parts += [json.dumps(e, ensure_ascii=False) for e in page["engine"]]
    parts.append("\n【版面检测区域】")
    parts += [json.dumps(r, ensure_ascii=False) for r in page.get("regions", [])]
    return "\n".join(parts)


def _shown(line: dict) -> dict:
    """A line as the model sees it: its facts, and ``copy_as`` — what copying it places — in place of the program's
    script candidates (the "_" keys are the program's own)."""
    shown = {k: v for k, v in line.items() if not k.startswith("_") and k != "scripts"}
    if line.get("_scripted"):
        shown["copy_as"] = line["_scripted"]
    return shown


def feedback(problems: list[str], answer: str) -> str:
    """What is told back with the one retry: the problems found and the answer they were found in."""
    shown = problems[:40]
    more = f"\n（另有 {len(problems) - len(shown)} 处，同类）" if len(problems) > len(shown) else ""
    return ("\n\n【上一次的回答没有通过检查】\n" + "\n".join(f"- {p}" for p in shown) + more
            + "\n请改正这些问题，重新输出完整的 JSON（不是只输出改动的部分）。上一次的回答：\n" + answer)


# ── checks ───────────────────────────────────────────────────────────────

_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")
_REFS = {u: re.compile(rf"{u}(\d+)(?:\s*[-–—~]\s*{u}?(\d+))?") for u in ("L", "K")}
NOUN = {"L": "行", "K": "引擎块"}  # what a reference names: a text-layer line, or a scan engine block (scanned pages)


def unit_of(page: dict) -> str:
    """The page's allocation unit: ``L`` text-layer lines, ``K`` the scan engine's blocks (``s0_inputs``)."""
    return page.get("unit", "L")


@dataclass
class Checked:
    """One answer through the three checks."""

    level: str  # "empty" | "unparseable" | "schema" | "invariants" | "valid"
    data: dict | None = None
    problems: list[str] = field(default_factory=list)


def parse(text: str) -> Any:
    body = _FENCE.sub("", (text or "").strip())
    value, _end = json.JSONDecoder().raw_decode(body)
    return value


def check(text: str, page: dict) -> Checked:
    if not (text or "").strip():
        return Checked("empty", problems=["回答是空的"])
    try:
        data = parse(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return Checked("unparseable", problems=[f"不是合法的 JSON：{exc}"])
    errors = sorted(_VALIDATOR.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        return Checked("schema", data=data if isinstance(data, dict) else None,
                       problems=[f"不符合 schema：{'/'.join(map(str, e.path)) or '根'}：{e.message}" for e in errors[:40]])
    problems = invariant_problems(data, page)
    return Checked("invariants" if problems else "valid", data=data, problems=problems)


def expand(ref: str, unit: str = "L") -> list[int] | None:
    """Numbers of a reference ("L3", "L3-L9"; "K3" on a scanned page); None when it is not one."""
    match = _REFS[unit].fullmatch(ref.strip())
    if not match:
        return None
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else start
    return list(range(start, end + 1)) if end >= start else None


def allocations(data: dict, unit: str = "L") -> list[tuple[str, str, int | None]]:
    """(where, ref, line) for every line reference, in order: blocks' parts, then aside; line None for a bad ref."""
    out = []
    for block in data.get("blocks", []):
        for k, part in enumerate(block.get("parts", [])):
            where = f"{block.get('id')} 的第 {k + 1} 个 {part.get('kind')}"
            for ref in part.get("lines", []):
                nums = expand(ref, unit)
                out += [(where, ref, n) for n in nums] if nums else [(where, ref, None)]
    for k, item in enumerate(data.get("aside", [])):
        for ref in item.get("lines", []):
            nums = expand(ref, unit)
            out += [(f"aside 第 {k + 1} 项", ref, n) for n in nums] if nums else [(f"aside 第 {k + 1} 项", ref, None)]
    return out


def unmapped(text: str) -> list[str]:
    return [f"U+{ord(ch):04X}" for ch in text if 0xE000 <= ord(ch) <= 0xF8FF or ord(ch) >= 0xF0000 or ch == "\ufffd"]


def latex_problems(text: str) -> list[str]:
    """Unbalanced ``$``, braces, or environments: what a renderer cannot read."""
    problems = []
    dollars = len(re.findall(r"(?<!\\)\$", text))
    if dollars % 2:
        problems.append("$ 不成对")
    depth = 0
    for match in re.finditer(r"(?<!\\)[{}]", text):
        depth += 1 if match.group() == "{" else -1
        if depth < 0:
            break
    if depth:
        problems.append("花括号不配对")
    begins, ends = re.findall(r"\\begin\{(\w+\*?)\}", text), re.findall(r"\\end\{(\w+\*?)\}", text)
    if sorted(begins) != sorted(ends):
        problems.append("\\begin 与 \\end 不配对")
    return problems


def invariant_problems(data: dict, page: dict) -> list[str]:
    total = len(page["lines"])
    u, noun = unit_of(page), NOUN[unit_of(page)]
    engine_ids = {e["id"] for e in page.get("engine") or []}
    width, height = page["image"]["width"], page["image"]["height"]
    problems: list[str] = []
    ids = [b["id"] for b in data["blocks"]]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"块 id {dup} 重复")
    seen: dict[int, str] = {}
    for where, ref, n in allocations(data, u):
        if n is None:
            problems.append(f"{where}：{ref!r} 不是{noun}号或{noun}号区间")
        elif not 1 <= n <= total:
            problems.append(f"{where}：{u}{n} 不存在（这页只有 {u}1–{u}{total}）" if total
                            else f"{where}：这页没有{'文字层' if u == 'L' else '引擎块'}，{ref} 不存在")
        elif n in seen:
            problems.append(f"{u}{n} 出现了不止一次（{seen[n]}；{where}）")
        else:
            seen[n] = where
    missing = [n for n in range(1, total + 1) if n not in seen]
    if missing:
        problems.append(f"没有分配的{noun}：{', '.join(compress(missing, u))}")
    for block in data["blocks"]:
        bid, parts = block["id"], block["parts"]
        if not parts and block["type"] != "figure":
            problems.append(f"{bid} 没有内容（只有 figure 可以没有 parts）")
        if block["type"] == "figure" and block["region"] is None:
            problems.append(f"{bid} 是图，要给出 region")
        region = block["region"]
        if region is not None and (len(region) != 4 or not (0 <= region[0] < region[2] <= width + 2
                                                               and 0 <= region[1] < region[3] <= height + 2)):
            problems.append(f"{bid} 的 region {region} 不是页面图里的框 [x0, y0, x1, y1]")
        for k, part in enumerate(parts, 1):
            where = f"{bid} 的第 {k} 个 {part['kind']}"
            if part["kind"] in ("copy", "table") and not part["lines"]:
                problems.append(f"{where} 没有 lines")
            if part["kind"] == "cell":
                problems += [f"{where}：{p}" for p in cell_problems(part, block, page)]
            if part["kind"] == "write":
                if not part["text"].strip() and (block["type"] != "formula" or u != "L"):  # a native formula's LaTeX is asked apart
                    problems.append(f"{where} 的 text 是空的")
                if not part["lines"] and region is None:
                    problems.append(f"{where} 不替换任何{noun}，块要给出 region")
                for p in latex_problems(part["text"]):
                    problems.append(f"{where}：{p}")
                if unmapped(part["text"]):
                    problems.append(f"{where} 写进了映射不到的字符 {', '.join(sorted(set(unmapped(part['text']))))}"
                                    f"：照图写出，看不清写 {UNREADABLE}")
            for e in part["engine"]:
                if e not in engine_ids:
                    problems.append(f"{where} 引用的引擎条目 {e} 不存在")
    for k, item in enumerate(data["aside"], 1):
        if item["to"] != EXCLUDED and item["to"] not in ids:
            problems.append(f"aside 第 {k} 项的 to {item['to']!r} 不是 excluded，也不是某个块的 id")
    return problems


def compress(numbers: list[int], unit: str = "L") -> list[str]:
    """References for sorted numbers, runs as ranges."""
    out, start = [], None
    for i, n in enumerate(numbers):
        if start is None:
            start = n
        if i + 1 == len(numbers) or numbers[i + 1] != n + 1:
            out.append(f"{unit}{start}" if start == n else f"{unit}{start}-{unit}{n}")
            start = None
    return out


# ── repair ───────────────────────────────────────────────────────────────


def repair(data: dict, page: dict) -> tuple[dict, list[str]]:
    """A schema-valid answer made to keep the line invariant, mechanically; (repaired, what was done)."""
    total = len(page["lines"])
    u = unit_of(page)
    engine_ids = {e["id"] for e in page.get("engine") or []}
    done: list[str] = []
    data = json.loads(json.dumps(data))
    seen: set[int] = set()

    def keep(refs: list[str], where: str) -> list[str]:
        kept: list[int] = []
        for ref in refs:
            nums = expand(ref, u)
            if nums is None:
                done.append(f"{where}：去掉不是{NOUN[u]}号的 {ref!r}")
                continue
            for n in nums:
                if not 1 <= n <= total:
                    done.append(f"{where}：去掉不存在的 {u}{n}")
                elif n in seen:
                    done.append(f"{where}：{u}{n} 已在前面分配，去掉这一处")
                else:
                    seen.add(n)
                    kept.append(n)
        return [f"{u}{n}" for n in kept]

    for block in data["blocks"]:
        for part in block["parts"]:
            part["lines"] = keep(part["lines"], block["id"])
            bad = [e for e in part["engine"] if e not in engine_ids]
            if bad:
                done.append(f"{block['id']}：去掉不存在的引擎条目 {', '.join(bad)}")
                part["engine"] = [e for e in part["engine"] if e in engine_ids]
        bad_cells = [p for p in block["parts"] if p["kind"] == "cell" and cell_problems(p, block, page)]
        if bad_cells:
            done.append(f"{block['id']}：去掉位置不对的 cell {', '.join(repr(p.get('cell')) for p in bad_cells)}")
        block["parts"] = [p for p in block["parts"] if (p["lines"] or p["kind"] in ("write", "cell")) and p not in bad_cells]
    for item in data["aside"]:
        item["lines"] = keep(item["lines"], "aside")
    data["aside"] = [a for a in data["aside"] if a["lines"]]
    missing = [n for n in range(1, total + 1) if n not in seen]
    for run in _runs(missing):
        after = _holder(data, run[0], u)
        block = {"id": _free_id(data), "type": "text", "level": None, "region": None, "repaired": True,
                 "parts": [{"kind": "copy", "lines": [f"{u}{n}" for n in run], "text": "", "engine": [], "cell": None}]}
        data["blocks"].insert(after + 1, block)
        done.append(f"补回没有分配的 {', '.join(compress(run, u))}：复制为 {block['id']}，放在 "
                    + (data['blocks'][after]['id'] + " 之后" if after >= 0 else "最前"))
    return data, done


def _runs(numbers: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for n in numbers:
        if runs and runs[-1][-1] == n - 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return runs


def _holder(data: dict, line: int, unit: str = "L") -> int:
    """Index of the block holding the nearest line before *line* (by line number), -1 when none does."""
    best, at = 0, -1
    for i, block in enumerate(data["blocks"]):
        for part in block["parts"]:
            for ref in part["lines"]:
                n = (expand(ref, unit) or [0])[-1]
                if best < n < line:
                    best, at = n, i
    return at


def _free_id(data: dict) -> str:
    taken = {b["id"] for b in data["blocks"]}
    k = len(taken) + 1
    while f"B{k}" in taken:
        k += 1
    return f"B{k}"


def fallback(page: dict) -> dict:
    """The allocation of a page whose answer never parsed: every line copied, a paragraph per PDF text block."""
    blocks: list[dict] = []
    previous = None
    for k, line in enumerate(page["lines"], 1):
        if line.get("_blk") != previous or not blocks:
            blocks.append({"id": f"B{len(blocks) + 1}", "type": "text", "level": None, "region": None,
                           "repaired": True, "parts": [{"kind": "copy", "lines": [], "text": "", "engine": [], "cell": None}]})
            previous = line.get("_blk")
        blocks[-1]["parts"][0]["lines"].append(f"{unit_of(page)}{k}")
    return {"blocks": blocks, "aside": [], "unresolved": []}


# ── render ───────────────────────────────────────────────────────────────


def visible(text: str) -> str:
    """Unmapped code points as the visible mark (the original stays in the evidence: the input record)."""
    return "".join(UNREADABLE if unmapped(ch) else ch for ch in text)


def render(data: dict, page: dict) -> str:
    """The page's Markdown."""
    u = unit_of(page)
    lines = {f"{u}{k}": line["text"] for k, line in enumerate(page["lines"], 1)}
    scripted = {f"{u}{k}": line.get("_scripted", line["text"]) for k, line in enumerate(page["lines"], 1)}
    tables = page.get("tables") or []
    used_tables: set[str] = set()
    out: list[str] = []
    for block in data["blocks"]:
        kind = block["type"]
        if kind == "figure":  # text read inside it as the pipeline writes it (render/markdown.py, IO6-5)
            src = f"{page['page_id']}-{block['id']}.png"
            out.append(f"![图]({src})")
            # its in-image text: what was written, and text copied into it (an engine's text block inside the figure)
            written = "\n\n".join(t for t in (visible(p["text"] if p["kind"] == "write" else join_wrapped(
                [lines[r] for ref in p["lines"] for r in _names(ref, u) if r in lines])).strip()
                for p in block["parts"] if p["kind"] in ("write", "copy")) if t)
            if written:
                quoted = "\n".join(f"> {ln}" if ln else ">" for ln in ("**〔图片识别〕**\n" + written).split("\n"))
                out.append(f'<!-- parserx:image-text src="{src}" -->\n{quoted}\n<!-- /parserx:image-text -->')
            continue
        pieces: list[str] = []
        for part in block["parts"]:
            if part["kind"] == "copy":  # the lines as the program's script candidates write them
                source = scripted
                pieces.append(join_wrapped([source[r] for ref in part["lines"] for r in _names(ref, u) if r in source]))
            elif part["kind"] == "write":
                pieces.append(part["text"].strip())
            elif part["kind"] == "cell":
                continue  # written into its table's grid
            else:
                refs = {r for ref in part["lines"] for r in _names(ref, u)}
                grids = [t for t in tables if refs & set(t["lines"])]  # one grid may hold two tables the model split
                for t in grids:
                    if t["block"] not in used_tables:
                        used_tables.add(t["block"])
                        out.append(write_cells(_scripted_cells(t["html"], refs, page),
                                               cells_of(block, page).get(tables.index(t), {})))
                rest = sorted(refs - {r for t in grids for r in t["lines"]}, key=lambda r: int(r[1:]))
                if rest and grids:  # lines the model put in the table that the extraction's grid does not hold
                    out.append(join_wrapped([lines[r] for r in rest if r in lines]))
                elif rest:  # no grid there: the lines, one per row
                    out.append("\n\n".join(lines[r] for r in rest if r in lines))
        text = visible(join_wrapped(pieces)) if pieces else ""
        if not text:
            continue
        if kind == "title":
            out.append("#" * min(max(block.get("level") or 2, 1), 6) + " " + text)
        elif kind == "formula":
            body = text.strip()
            body = body[2:-2].strip() if body.startswith("$$") and body.endswith("$$") else body
            out.append(f"$$\n{body}\n$$")
        else:
            out.append(text)
    return "\n\n".join(out) + "\n"


_CELL = re.compile(r"(<t[dh][^>]*>)([^<]*)(</t[dh]>)")


def _scripted_cells(table_html: str, names: set[str], page: dict) -> str:
    """The grid with each cell that is exactly one of *names*' lines (spacing aside: the grid spaces a cell's glyphs
    its own way) written with that line's script candidates."""
    todo = {}
    for k, line in enumerate(page["lines"], 1):
        if f"{unit_of(page)}{k}" in names and "_scripted" in line:
            todo.setdefault("".join(line["text"].split()), []).append(
                "".join(part if part.startswith("<") else html.escape(part, quote=False)
                        for part in re.split(r"(</?su[pb]>)", line["_scripted"])))

    def cell(match: re.Match) -> str:
        key = "".join(html.unescape(match.group(2)).split())
        return match.group(1) + todo[key].pop(0) + match.group(3) if todo.get(key) else match.group(0)

    return _CELL.sub(cell, table_html)


_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<(t[dh])\b([^>]*)>(.*?)</\1>", re.S | re.I)
_ADDRESS = re.compile(r"^\s*T(\d+)\s*[:：]\s*(\d+)\s*[:：]\s*(\d+)\s*$")


def _span(attrs: str, name: str) -> int:
    match = re.search(rf'{name}\s*=\s*"?(\d+)', attrs, re.I)
    return max(1, int(match.group(1))) if match else 1


def table_cells(table_html: str) -> list[dict]:
    """The cells of an HTML table as the HTML table model lays them out: row, column (0-based, a spanned position
    taken by the cell that covers it), spans, the cell's inner HTML and where it stands in the string."""
    out, taken = [], set()
    for r, row in enumerate(_ROW.finditer(table_html)):
        c = 0
        for m in _TD.finditer(table_html, row.start(1), row.end(1)):
            while (r, c) in taken:
                c += 1
            rs, cs = _span(m.group(2), "rowspan"), _span(m.group(2), "colspan")
            taken |= {(r + i, c + j) for i in range(rs) for j in range(cs)}
            out.append({"row": r, "col": c, "rowspan": rs, "colspan": cs, "inner": m.group(3),
                        "at": (m.start(3), m.end(3))})
            c += cs
    return out


def grid_rows(table_html: str) -> list[list[str | None]]:
    """The table as rows of cells (inner HTML), null where a spanned cell above or to the left covers the place."""
    cells = table_cells(table_html)
    rows = max((x["row"] + x["rowspan"] for x in cells), default=0)
    cols = max((x["col"] + x["colspan"] for x in cells), default=0)
    grid: list[list[str | None]] = [[None] * cols for _ in range(rows)]
    for x in cells:
        grid[x["row"]][x["col"]] = x["inner"]
    return grid


def cell_address(part: dict) -> tuple[int, int, int] | None:
    """(table index, row, column), all 0-based, of a ``cell`` part's address; None when it is not one."""
    match = _ADDRESS.match(part.get("cell") or "")
    return (int(match.group(1)) - 1, int(match.group(2)) - 1, int(match.group(3)) - 1) if match else None


def cell_problems(part: dict, block: dict, page: dict) -> list[str]:
    tables = page.get("tables") or []
    address = cell_address(part)
    problems = []
    if part["lines"]:
        problems.append("cell 的 lines 要是空列表（这张表的行都在 table 里）")
    if not part["text"].strip():
        problems.append("cell 的 text 是空的")
    if address is None:
        return problems + [f"cell {part.get('cell')!r} 不是 \"T表号:行:列\" 的位置"]
    t, r, c = address
    if not 0 <= t < len(tables):
        return problems + [f"没有表 T{t + 1}（这页程序建出了 {len(tables)} 张表）"]
    if not any(x["row"] == r and x["col"] == c for x in table_cells(tables[t]["html"])):
        problems.append(f"表 T{t + 1} 没有第 {r + 1} 行第 {c + 1} 列这一格")
    u = unit_of(page)
    own = {x for p in block["parts"] if p["kind"] == "table" for ref in p["lines"] for x in _names(ref, u)}
    if not own & {x for ref in tables[t]["lines"] for x in _names(ref, u)}:
        problems.append(f"改表 T{t + 1} 的格子要放在它的 table 所在的块里")
    for p in latex_problems(part["text"]):
        problems.append(p)
    if unmapped(part["text"]):
        problems.append(f"写进了映射不到的字符：照图写出，看不清写 {UNREADABLE}")
    return problems


def cells_of(block: dict, page: dict) -> dict[int, dict[tuple[int, int], str]]:
    """The block's written cells: table index → (row, column) → text."""
    out: dict[int, dict[tuple[int, int], str]] = {}
    for part in block["parts"]:
        address = cell_address(part) if part["kind"] == "cell" else None
        if address is not None and part["text"].strip():
            out.setdefault(address[0], {})[(address[1], address[2])] = part["text"].strip()
    return out


def write_cells(table_html: str, written: dict[tuple[int, int], str]) -> str:
    """The table with the written cells' contents in place (their text escaped as HTML, sub- and superscript tags
    kept)."""
    if not written:
        return table_html
    out = table_html
    for x in sorted(table_cells(table_html), key=lambda x: x["at"][0], reverse=True):
        text = written.get((x["row"], x["col"]))
        if text is not None:
            safe = "".join(p if re.fullmatch(r"</?su[pb]>", p) else html.escape(p, quote=False)
                           for p in re.split(r"(</?su[pb]>)", text))
            out = out[:x["at"][0]] + safe + out[x["at"][1]:]
    return out


def _names(ref: str, unit: str = "L") -> list[str]:
    return [f"{unit}{n}" for n in expand(ref, unit) or []]


def destinations(data: dict, page: dict) -> dict[str, str]:
    """Line → where it ended up: "copy:B3", "write:B4", "table:B6", "aside:excluded" or "aside:B7"."""
    out: dict[str, str] = {}
    u = unit_of(page)
    for block in data["blocks"]:
        for part in block["parts"]:
            for ref in part["lines"]:
                for name in _names(ref, u):
                    out.setdefault(name, f"{part['kind']}:{block['id']}")
    for item in data["aside"]:
        for ref in item["lines"]:
            for name in _names(ref, u):
                out.setdefault(name, f"aside:{item['to']}")
    return out
