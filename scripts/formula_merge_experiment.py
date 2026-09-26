"""Q70 experiment: blocks whose page reading lacks text-layer characters, merged by a VLM editor (image + both
readings); does the merge keep every character?  Usage: formula_merge_experiment.py OUT_DIR DOC (after formula_page_experiment)."""
import sys, tempfile, json, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1])); sys.path.insert(0, str(Path(__file__).resolve().parent))
import pymupdf
from formula_page_experiment import lost, _centre_in
from parserx.config.schema import load_config
from parserx.content import scan
from parserx.ir.anchor import PdfAnchor
from parserx.reading.compare import normalize
from parserx.scheduling import run_ordered
from parserx.tools import workspace_init
from parserx.tools.context import ToolContext
from parserx.workspace import Workspace
S = Path(sys.argv[1]); doc = sys.argv[2]
config = load_config(str(Path(__file__).resolve().parents[1] / "configs" / "regression_v2.yaml"))
config.cache.mode, config.cache.dir = "read_write", str(S / "fpage" / "cache")
gt = Path(__file__).resolve().parents[1] / "ground_truth" / doc
tmp = Path(tempfile.mkdtemp()); workspace_init(gt / "input.pdf", tmp / "ws", config=config)
ws = Workspace.open(tmp / "ws"); ctx = ToolContext(ws, config); state = ws.load()
PROMPT = ("你是编辑。图中是文档的一段。给你两份读数：A 是 PDF 文字层（字符准确，但公式的上下标、分式结构丢失，个别字形可能是乱码），"
          "B 是 OCR（有 LaTeX 结构，个别字符可能认错、可能漏掉公式编号）。请以图为准，输出这段的最终文字：正文照抄，公式用 LaTeX（行内 $…$，"
          "行间 $$…$$），字符以 A 为准、结构以 B 为准。只输出结果。")
tasks = []
with pymupdf.open(gt / "input.pdf") as pdf:
    pages = list(range(1, pdf.page_count + 1))
    results = ctx.ocr().recognize_pdf(scan.batch_pdf(pdf, pages))
    native = [b for b in state.blocks if b.kind.value == "text" and b.text and isinstance(b.anchors[0], PdfAnchor)]
    for n, res in zip(pages, results):
        pr = res.raw["layoutParsingResults"][0].get("prunedResult", {})
        k = pdf[n - 1].rect.width / (pr.get("width") or pdf[n - 1].rect.width)
        for e in pr.get("parsing_res_list", []):
            content = str(e.get("block_content", ""))
            if "$" not in content: continue
            box = tuple(v * k for v in e.get("block_bbox", [0, 0, 0, 0]))
            under = [b for b in native if b.anchors[0].page == n and _centre_in(b.anchors[0].bbox, box)]
            if not under: continue
            text = "\n".join(b.text for b in under)
            miss = sum(lost(text, content).values())
            if miss == 0: continue
            path = S / f"merge_{doc}_{len(tasks)}.png"
            path.write_bytes(pdf[n - 1].get_pixmap(dpi=200, clip=pymupdf.Rect(box) + (-4, -4, 4, 4)).tobytes("png"))
            tasks.append({"path": path, "native": text, "ocr": content, "miss": miss, "lost": "".join(sorted(lost(text, content).elements()))})
vlm = ctx.vlm("low")
t0 = time.monotonic()
outs = run_ordered(tasks, lambda t: vlm.call("describe_image", t["path"], PROMPT, context=f"A：\n{t['native']}\n\nB：\n{t['ocr']}",
                                             temperature=0.0, max_tokens=4096, structured_output_mode="off",
                                             json_schema_name="parserx_merge"), max_workers=6)
print(f"{len(tasks)} blocks with lost characters, VLM {time.monotonic() - t0:.0f} s")
fixed_all = 0
for t, o in zip(tasks, outs):
    merged = str(o.value or "")
    after = lost(t["native"], merged)
    fixed_all += not after
    print(f"lost {t['miss']:3d} {t['lost'][:20]!r:24s} → after merge {sum(after.values()):3d} {''.join(sorted(after.elements()))[:20]!r}")
print("merged keeps every text-layer character:", fixed_all, "of", len(tasks))
json.dump([{**{k: v for k, v in t.items() if k != "path"}, "merged": str(o.value or "")} for t, o in zip(tasks, outs)],
          open(S / f"merge_{doc}.json", "w"), ensure_ascii=False, indent=1)
