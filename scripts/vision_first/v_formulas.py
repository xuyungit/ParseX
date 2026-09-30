"""The LaTeX of an allocation's display formulas, asked apart from the allocation (Q143 ②, contract v5).

The allocation names each display formula's lines and engine entries and leaves its text empty.  One more request per
page then shows the model each formula alone — the page rendered at ``CROP_DPI`` around the formula's lines, engine
entries and region — with its text-layer fragments (the characters, without the structure) and the engine's readings
(the structure, a character sometimes wrong), and asks for the LaTeX only.  On paper_chn01 pp. 3–5 luna dropped or
repeated pieces of formulas it wrote while allocating, and wrote the same formulas well when it only wrote.

Checked like the allocation (JSON, schema, every formula exactly once, LaTeX balanced, no unmapped code point), one
retry with the problems.  A formula still without LaTeX keeps an empty text: the adapter's conservative default then
takes the engine reading whose numbers agree with the lines (common plan §3.4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
from jsonschema import Draft202012Validator

import p0_contract as contract

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["formulas"], "properties": {
    "formulas": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["id", "latex"],
                                            "properties": {"id": {"type": "string"}, "latex": {"type": "string"}}}}}}
_VALIDATOR = Draft202012Validator(SCHEMA)
CROP_DPI = 300  # twice the allocation's page image: scripts and primes legible
MARGIN = 6  # pixels of the allocation's page image around a formula's boxes

INSTRUCTIONS = """你在为文档的一页写行间公式的 LaTeX。下面依次给出这一页的 {n} 个行间公式的图，第 k 张图是【公式】里第 k 个公式。每个公式另附：
- text_layer：PDF 文字层里这个公式的文字，按行给出。字符准确，但上下标、分式、根号的结构散了。
- engine：另一个识别工具读出的 LaTeX。结构通常对，个别字符可能认错，可能漏掉编号；一条读数可能连着相邻的几个公式，只取图里这个公式的部分。

照图写出每个公式的 LaTeX：
- 字符以文字层为准，结构以图为准；
- 式子的写法也照图，例如图上是 (…)^{{-1}} 就不要改写成分式；
- 公式编号写成 \\tag{{n}}，不加 $$；
- 图上看不清的地方写 〔?〕，不要猜。

只写图里这个公式本身，不补全，不把几个公式合并，也不把一个公式拆开。

只输出一个 JSON 对象：{{"formulas": [{{"id": "B3", "latex": "…"}}]}}，每个公式恰好一项，id 用【公式】里给的。"""


def wanted(data: dict) -> list[dict]:
    """The allocation's display formulas: id, lines (names), engine entries, region."""
    out = []
    for block in data["blocks"]:
        if block["type"] != "formula":
            continue
        names = [x for p in block["parts"] for ref in p["lines"] for x in contract._names(ref)]
        engine = [e for p in block["parts"] for e in p["engine"]]
        out.append({"id": block["id"], "lines": names, "engine": list(dict.fromkeys(engine)),
                    "region": block.get("region")})
    return out


def crop_box(page: dict, formula: dict) -> list[float] | None:
    """The formula's box on the allocation's page image: its lines and region, with a margin; its engine entries only
    when it has neither (the engine may read two neighbouring formulas as one entry: its box would show both, and
    the model wrote the first twice)."""
    lines = page["lines"]
    boxes = [lines[int(x[1:]) - 1]["box"] for x in formula["lines"] if 1 <= int(x[1:]) <= len(lines)]
    if formula.get("region"):
        boxes.append(formula["region"])
    if not boxes:
        entries = {e["id"]: e for e in page.get("engine") or []}
        boxes = [entries[e]["box"] for e in formula["engine"] if e in entries]
    if not boxes:
        return None
    width, height = page["image"]["width"], page["image"]["height"]
    return [max(0, min(b[0] for b in boxes) - MARGIN), max(0, min(b[1] for b in boxes) - MARGIN),
            min(width, max(b[2] for b in boxes) + MARGIN), min(height, max(b[3] for b in boxes) + MARGIN)]


def crops(source: Path, page: dict, formulas: list[dict], out_dir: Path) -> list[Path | None]:
    """One PNG per formula, rendered at CROP_DPI from the PDF (None for a formula with nothing to show)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    k = 72.0 / page["image"]["dpi"]
    paths: list[Path | None] = []
    with pymupdf.open(source) as doc:
        pdf_page = doc[page["page"] - 1]
        for formula in formulas:
            box = crop_box(page, formula)
            if box is None:
                paths.append(None)
                continue
            clip = pymupdf.Rect(*(v * k for v in box)) * pdf_page.derotation_matrix
            path = out_dir / f"{page['page_id']}-{formula['id']}.png"
            path.write_bytes(pdf_page.get_pixmap(dpi=CROP_DPI, clip=clip).tobytes("png"))
            paths.append(path)
    return paths


def prompt(n: int) -> str:
    return INSTRUCTIONS.format(n=n)


def context(page: dict, formulas: list[dict]) -> str:
    lines = page["lines"]
    entries = {e["id"]: e for e in page.get("engine") or []}
    out = ["【公式】（每个一个 JSON，顺序与图相同）"]
    for formula in formulas:
        out.append(json.dumps({
            "id": formula["id"],
            "text_layer": [lines[int(x[1:]) - 1]["text"] for x in formula["lines"] if 1 <= int(x[1:]) <= len(lines)],
            "engine": [entries[e]["text"] for e in formula["engine"] if e in entries]}, ensure_ascii=False))
    return "\n".join(out)


def check(text: str, ids: list[str]) -> contract.Checked:
    if not (text or "").strip():
        return contract.Checked("empty", problems=["回答是空的"])
    try:
        data = contract.parse(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return contract.Checked("unparseable", problems=[f"不是合法的 JSON：{exc}"])
    errors = list(_VALIDATOR.iter_errors(data))
    if errors:
        return contract.Checked("schema", data=data if isinstance(data, dict) else None,
                                problems=[f"不符合 schema：{e.message}" for e in errors[:10]])
    problems = []
    answered = [f["id"] for f in data["formulas"]]
    for i in ids:
        if answered.count(i) != 1:
            problems.append(f"公式 {i} 要恰好一项（现在 {answered.count(i)} 项）")
    problems += [f"{i} 不是要写的公式" for i in answered if i not in ids]
    for f in data["formulas"]:
        if not f["latex"].strip():
            problems.append(f"{f['id']} 的 latex 是空的")
        problems += [f"{f['id']}：{p}" for p in contract.latex_problems(f["latex"])]
        if contract.unmapped(f["latex"]):
            problems.append(f"{f['id']} 写进了映射不到的字符：照图写出，看不清写 {contract.UNREADABLE}")
    return contract.Checked("invariants" if problems else "valid", data=data, problems=problems)


def usable(checked: contract.Checked, ids: list[str]) -> dict[str, str]:
    """id → LaTeX from an answer, as far as it goes: the formulas answered once, non-empty and balanced."""
    if not checked.data or "formulas" not in checked.data:
        return {}
    answered = [f["id"] for f in checked.data["formulas"]]
    return {f["id"]: f["latex"].strip() for f in checked.data["formulas"]
            if f["id"] in ids and answered.count(f["id"]) == 1 and f["latex"].strip()
            and not contract.latex_problems(f["latex"]) and not contract.unmapped(f["latex"])}


def fill(data: dict, latex: dict[str, str]) -> dict:
    """The allocation with each formula block one ``write`` part: all its lines and engine entries, the LaTeX (the
    allocation's own text when the formula request gave none)."""
    data = json.loads(json.dumps(data))
    for block in data["blocks"]:
        if block["type"] != "formula":
            continue
        lines = [ref for p in block["parts"] for ref in p["lines"]]
        engine = list(dict.fromkeys(e for p in block["parts"] for e in p["engine"]))
        kept = next((p["text"].strip() for p in block["parts"] if p["kind"] == "write" and p["text"].strip()), "")
        block["parts"] = [{"kind": "write", "lines": lines, "text": latex.get(block["id"]) or kept, "engine": engine,
                           "cell": None}]
    return data


def run(caller, run_n: int, run_dir: Path, page: dict, data: dict, *, max_tokens: int, source: Path) -> dict:
    """The formula request for one page (two rounds at most); the record, with ``latex`` by formula id."""
    formulas = wanted(data)
    # per configuration: another configuration's formula of the same page and id is another crop (runs in parallel
    # wrote one file each other's requests read)
    paths = crops(source, page, formulas, run_dir / "formulas" / caller.name)
    shown = [(f, p) for f, p in zip(formulas, paths) if p is not None]
    ids = [f["id"] for f, _ in shown]
    record: dict = {"formulas": ids, "rounds": [], "latex": {}, "status": "none"}
    if not shown:
        return record
    ask_prompt, ask_context = prompt(len(shown)), context(page, [f for f, _ in shown])
    feedback = ""
    for round_ in (1, 2):
        answer = caller.ask([p for _, p in shown], ask_prompt, ask_context, schema=SCHEMA, max_tokens=max_tokens,
                            run=run_n, round_=round_, feedback=feedback, method="v_formulas")
        checked = check(answer.get("text", ""), ids)
        record["rounds"].append({"round": round_, **answer, "level": checked.level, "problems": checked.problems})
        record["latex"] = {**usable(checked, ids), **record["latex"]} if round_ == 2 else usable(checked, ids)
        if checked.level == "valid":
            record["status"] = "first_valid" if round_ == 1 else "valid_after_retry"
            break
        feedback = contract.feedback(checked.problems, answer.get("text", ""))
    else:
        record["status"] = "partial" if record["latex"] else "failed"
    record["missing"] = [i for i in ids if i not in record["latex"]]
    return record
