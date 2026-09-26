"""Usage: uv run python scripts/formula_engines.py DOC1,DOC2 OUT_DIR

Q70 experiment: formula regions of native PDF pages → LaTeX by (a) the scan engine, (b) the VLM on the image,
(c) the VLM on the image with the text layer's characters.  Scored against expected.md's display formulas."""
import io
import json
import re
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import pymupdf  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from parserx.config.schema import load_config  # noqa: E402
from parserx.content import scan  # noqa: E402
from parserx.layout.detector import detect_cached  # noqa: E402
from parserx.reading.compare import normalize  # noqa: E402
from parserx.scheduling import run_ordered  # noqa: E402
from parserx.tools import workspace_init  # noqa: E402
from parserx.tools.context import ToolContext  # noqa: E402
from parserx.workspace import Workspace  # noqa: E402

DOCS = sys.argv[1].split(",")
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
config = load_config(REPO / "configs" / "regression_v2.yaml")
config.cache.mode, config.cache.dir = "read_write", str(OUT / "cache")

PROMPT_IMAGE = ("把图中的数学公式转写为 LaTeX。只输出 LaTeX 本身：不要解释，不要加 $ 或 $$。"
                "照图转写，不补全、不改写；公式编号（如 (3)）写成 \\tag{3}。")
PROMPT_HINT = PROMPT_IMAGE + ("\n下面是 PDF 文字层在这个区域里的字符，字符是准确的，但顺序与上下标位置可能错乱，"
                              "请以图为准排列结构、以这些字符确认具体符号：\n")

_DROP = re.compile(r"\\(left|right|mathrm|text|operatorname|displaystyle|mathit|mathbf|bm|boldsymbol|quad|qquad"
                   r"|,|;|!|:| )|[{}\s$&]|\\\\|\\begin\w*|\\end\w*|aligned|array|cases")


def canon(latex: str) -> str:
    latex = re.sub(r"\\tag\{([^}]*)\}", r"(\1)", latex or "")
    latex = re.sub(r"\\begin\{[a-z*]+\}|\\end\{[a-z*]+\}", "", latex)
    return _DROP.sub("", latex)


def display_formulas(md: str) -> list[str]:
    found = re.findall(r"\$\$(.+?)\$\$", md, flags=re.S)
    found += re.findall(r"\\begin\{(?:aligned|equation|array|cases)\}.+?\\end\{(?:aligned|equation|array|cases)\}",
                        md, flags=re.S)
    return [f.strip() for f in found if f.strip()]


rows, totals = [], {}
for doc in DOCS:
    gt = next(Path(g) / doc for g in (REPO / "ground_truth", REPO / "ground_truth_public") if (Path(g) / doc).is_dir())
    expected = display_formulas((gt / "expected.md").read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp())
    workspace_init(gt / "input.pdf", tmp / "ws", config=config)
    ctx = ToolContext(Workspace.open(tmp / "ws"), config)
    regions = []
    with pymupdf.open(gt / "input.pdf") as pdf:
        for page in pdf:
            dpi = config.layout.page_dpi
            png = page.get_pixmap(dpi=dpi).tobytes("png")
            for r in detect_cached(ctx.detector(), png, ctx.cache):
                if r.label != "display_formula":
                    continue
                rect = pymupdf.Rect(*(v * 72 / dpi for v in r.bbox)) + (-3, -3, 3, 3)
                native = page.get_text("text", clip=rect).strip()
                crop = page.get_pixmap(dpi=200, clip=rect)
                regions.append({"page": page.number + 1, "native": native, "png": crop.tobytes("png"),
                                "size": (crop.width, crop.height)})
    # the matching formula of the annotation, by the text layer's characters (independent of the engines)
    for reg in regions:
        scores = [(fuzz.ratio(normalize(reg["native"]), normalize(e)), e) for e in expected]
        best = max(scores, default=(0, None))
        reg["expected"] = best[1] if best[0] >= 50 else None
    regions = [r for r in regions if r["expected"]]
    for i, reg in enumerate(regions):
        path = OUT / f"{doc}_{i}.png"
        path.write_bytes(reg["png"])
        reg["path"] = path
    print(f"{doc}: {len(regions)} matched formula regions", flush=True)
    if not regions:
        continue
    # (a) scan engine: one batched job for the document's regions
    before = ctx.meter.snapshot()
    t0 = time.monotonic()
    results = ctx.ocr().recognize_pdf(scan.image_batch_pdf([(r["png"], *r["size"]) for r in regions]))
    t_ocr = time.monotonic() - t0
    for reg, res in zip(regions, results):
        entries = res.raw["layoutParsingResults"][0].get("prunedResult", {}).get("parsing_res_list", [])
        reg["ocr"] = " ".join(str(e.get("block_content", "")) for e in entries).replace("$$", " ").strip()
    ocr_snap = ctx.meter.snapshot()
    # (b), (c) VLM, concurrently per region
    vlm = ctx.vlm("none")
    timings = {}
    for mode, prompt_of in (("vlm", lambda r: PROMPT_IMAGE), ("vlm+text", lambda r: PROMPT_HINT + r["native"])):
        t0 = time.monotonic()
        outs = run_ordered(regions, lambda r: vlm.call("describe_image", r["path"], prompt_of(r), temperature=0.0,
                                                        max_tokens=2048, structured_output_mode="off",
                                                        json_schema_name="parserx_formula"), max_workers=6)
        timings[mode] = time.monotonic() - t0
        for reg, o in zip(regions, outs):
            reg[mode] = str(o.value or "").strip().strip("$").strip() if o.exception is None else ""
    after = ctx.meter.snapshot()
    for reg in regions:
        row = {"doc": doc, "page": reg["page"], "expected": reg["expected"], "native": reg["native"]}
        for mode in ("ocr", "vlm", "vlm+text"):
            row[mode] = reg.get(mode, "")
            row[mode + "_score"] = fuzz.ratio(canon(row[mode]), canon(reg["expected"]))
        rows.append(row)
    totals[doc] = {"regions": len(regions), "ocr_s": round(t_ocr, 1), "vlm_s": round(timings["vlm"], 1),
                   "vlm+text_s": round(timings["vlm+text"], 1),
                   "ocr_requests": ocr_snap.requests.get("ocr", 0) - before.requests.get("ocr", 0),
                   "vlm_requests": after.requests.get("vlm", 0) - ocr_snap.requests.get("vlm", 0),
                   "vlm_usd": round((after.cost_usd or 0) - (ocr_snap.cost_usd or 0), 4)}
    print(json.dumps(totals[doc]), flush=True)

(OUT / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
n = len(rows)
print(f"\n{n} regions")
for mode in ("ocr", "vlm", "vlm+text"):
    scores = [r[mode + "_score"] for r in rows]
    print(f"{mode:9s} mean {sum(scores) / n:.1f}  ≥90: {sum(s >= 90 for s in scores)}  <60: {sum(s < 60 for s in scores)}")
wins = {m: sum(1 for r in rows if r[m + "_score"] == max(r["ocr_score"], r["vlm_score"], r["vlm+text_score"]))
        for m in ("ocr", "vlm", "vlm+text")}
print("best per region (ties count for each):", wins)
