"""Scanned-page probe inputs (docs/v2_vision_first_scanned.md §2, §5.1): what the service model is given for one
scanned page.

- **Page image**: the page as shown at ``DPI``, as on a native page (``p0_inputs``); every box below is in its pixels.
- **The scan engine's blocks** (``K1``, ``K2`` … in the engine's order): the engine's own reading of the page
  (PaddleOCR-VL, the response the pipeline got — found in the V run's pipeline cache by the raw reference the
  workspace's blocks carry, the page's place in its batch by the pages sharing that reference), made into blocks by
  the pipeline's own ``scan.page_blocks``: the engine's label, box, reading (a table's grid, a formula's LaTeX).
  These are the units the answer allocates (``unit`` ``K``); ``text`` is what copying one places (a table's HTML, a
  formula as display math), ``shown`` what the model reads.
- **Local reading**: the pipeline's own reading of the page (RapidOCR, the workspace's ``readings``), lines with
  boxes — the second, independent reading the comparison checks written numbers against.
- **Layout detector regions**: hints.

    uv run python scripts/vision_first/s0_inputs.py --run-dir eval_runs/<run>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pymupdf  # noqa: E402

from p0_inputs import DPI, _px, _sha256, detector_regions, document, page_id  # noqa: E402

from parserx.cache import ResponseCache  # noqa: E402
from parserx.config.schema import load_config  # noqa: E402
from parserx.content import scan  # noqa: E402
from parserx.ir.enums import BlockKind, PageStatus  # noqa: E402
from parserx.ir.state import PageState  # noqa: E402

# Hard scanned pages of the tuning set (the draft's key errors per page, C1 report §7/§8): dense English text, a
# table page, two Chinese book pages, a poster page, a form page, a standard's table page.
PAGES: tuple[tuple[str, int], ...] = (
    ("omnidoc_academic_literature_en_text_01", 1), ("omnidoc_academic_literature_en_text_02", 1),
    ("omnidoc_academic_literature_en_table_02", 1), ("omnidoc_book_zh_text_01", 1), ("omnidoc_book_zh_text_02", 1),
    ("ocr01", 1), ("unseen_scan_form01", 3), ("ocr_scan_jtg3362", 4),
)
# The V runs whose workspaces hold each document's pipeline draft (and whose pipeline cache the engine response).
V_RUNS = {"ocr01": "2026-09-30_v6_vision_first"}
V_RUN = "2026-09-30_v6b_vision_first"
CONFIGURATION = "luna-medium-r1"


def engine_reading(doc: str, n: int) -> tuple[dict, str, str, Path]:
    """(the engine's reading of page *n*, its raw reference, the engine version, the workspace)."""
    run = REPO_ROOT / "eval_runs" / V_RUNS.get(doc, V_RUN)
    ws = run / "work" / CONFIGURATION / doc
    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    pages_of: dict[str, set[int]] = {}
    version = ""
    for block in state["blocks"]:
        for obs in block["observations"]:
            if obs.get("engine") == scan.ENGINE and obs.get("raw_ref") and obs["task"] == "recognize":
                pages_of.setdefault(obs["raw_ref"], set()).add(obs["anchor"]["page"])
                version = obs.get("engine_version") or version
    ref = next(r for r, pages in pages_of.items() if n in pages)
    found, response = ResponseCache(run / "pipeline_cache", "read_only").get("ocr", ref)
    if not found:
        raise FileNotFoundError(f"{doc} p{n}: engine response {ref} not in {run / 'pipeline_cache'}")
    results = response["layoutParsingResults"]
    batch = sorted(pages_of[ref])
    if len(batch) != len(results):
        raise ValueError(f"{doc} p{n}: {len(results)} page readings for pages {batch}")
    return results[batch.index(n)], ref, version, ws


def engine_units(page: pymupdf.Page, n: int, raw: dict, ref: str, version: str, scale: float) -> tuple[list, list]:
    """The engine's blocks as allocation units, and the engine's tables."""
    state = PageState(n=n, unit="pdf_page", status=PageStatus.DONE, size_pt=(page.rect.width, page.rect.height),
                      rotation=page.rotation)
    result = scan.page_blocks(scan.PageScan(page=n, raw=raw, raw_ref=ref, engine_version=version),
                              page_size=(page.rect.width, page.rect.height), first_seq=1, first_item=1, page=state)
    units, tables = [], []
    for k, block in enumerate(result.blocks, 1):
        uid = f"K{k}"
        obs = block.observations[0]
        if block.cells is not None:
            html = block.cells.to_html()
            text, shown = html, None
            tables.append({"block": uid, "lines": [uid], "html": html})
        elif block.kind == BlockKind.FORMULA:
            latex = (obs.text or "").strip().strip("$").strip()
            text, shown = f"$${latex}$$", latex
        else:
            text = shown = obs.text or ""
        units.append({"id": uid, "label": obs.label, "kind": block.kind.value,
                      "box": _px(pymupdf.Rect(block.anchors[0].bbox) * page.rotation_matrix, scale),
                      "text": text, "shown": shown, "_blk": k})
    return units, tables


def local_lines(ws: Path, n: int, page: pymupdf.Page, scale: float) -> list[dict]:
    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    reading = next((r for r in state.get("readings") or [] if r["n"] == n), None)
    if reading is None:
        return []
    return [{"box": _px(pymupdf.Rect(line["bbox"]) * page.rotation_matrix, scale), "text": line["text"]}
            for line in reading["lines"]]


def workspace_units(state, n: int, page: pymupdf.Page, scale: float) -> tuple[list, list]:
    """V (scanned pages by signal): the page's scan-engine blocks as the workspace holds them after ``recognize``
    — in their order, page furniture included — as allocation units; ``_block`` is the block each unit is."""
    from parserx.ir.anchor import AssetAnchor
    from parserx.ir.enums import BlockStatus, TaskKind
    from parserx.workspace.queries import block_unit, ordered

    def read(b):
        return next((o for o in b.observations if o.engine == scan.ENGINE and o.task == TaskKind.RECOGNIZE), None)

    blocks = [b for b in ordered(state) if block_unit(state, b) == n and b.status != BlockStatus.MERGED
              and not isinstance(b.anchors[0], AssetAnchor) and read(b) is not None]
    units, tables = [], []
    for k, block in enumerate(blocks, 1):
        uid = f"K{k}"
        if block.cells is not None:
            html = block.cells.to_html()
            text, shown = html, None
            tables.append({"block": uid, "lines": [uid], "html": html})
        elif block.kind == BlockKind.FORMULA:
            latex = (block.text or "").strip().strip("$").strip()
            text, shown = f"$${latex}$$", latex
        else:
            text = shown = block.text or ""
        units.append({"id": uid, "label": read(block).label, "kind": block.kind.value,
                      "box": _px(pymupdf.Rect(block.anchors[0].bbox) * page.rotation_matrix, scale),
                      "text": text, "shown": shown, "_blk": k, "_block": block.id})
    return units, tables


def local_reading(src: pymupdf.Document, n: int, config, derived) -> list[dict]:
    """The local reading of page *n* (the pipeline's reader and resolution, its derived cache), boxes in the
    pixels of the page image the model sees."""
    from parserx.reading.local import LocalReader, read_cached

    dpi = config.tools.reading_dpi
    png, _, _ = scan.render_page_at(src, n, dpi)
    k = DPI / dpi
    return [{"box": [round(v * k) for v in box], "text": text} for box, text, _ in read_cached(LocalReader(), png, derived)]


def from_workspace(run_dir: Path, doc: str, n: int, state, config, derived) -> dict:
    """The input record of scanned page *n* built from the V workspace (after ``recognize``), written to the run's
    inputs unless already there (configurations of one run read the same engine response)."""
    out_dir = run_dir / "inputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    pid = page_id(doc, n)
    path = document(doc)
    with pymupdf.open(path) as src:
        page = src[n - 1]
        png, width, height = scan.render_page_at(src, n, DPI)
        scale = DPI / 72.0
        units, tables = workspace_units(state, n, page, scale)
        record = {
            "page_id": pid, "document": doc, "page": n, "unit": "K", "input_sha256": _sha256(path.read_bytes()),
            "image": {"file": f"{pid}.png", "dpi": DPI, "width": width, "height": height, "rotation": page.rotation,
                      "size_pt": [round(page.rect.width, 2), round(page.rect.height, 2)]},
            "lines": units, "tables": tables, "engine": None,
            "engine_source": {"workspace": "V pass (recognize)", "engine": state.engines.get("ocr")},
            "local": local_reading(src, n, config, derived),
            "regions": detector_regions(page, config, derived),
        }
    target = out_dir / f"{pid}.json"
    if target.exists():  # the same page from an earlier configuration: its record must be this one
        if json.loads(target.read_text(encoding="utf-8"))["lines"] != record["lines"]:
            raise RuntimeError(f"{pid}: the engine blocks differ from the stored input")
        return json.loads(target.read_text(encoding="utf-8"))
    (out_dir / record["image"]["file"]).write_bytes(png)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(target)
    return record


def build(run_dir: Path, pages=PAGES) -> list[dict]:
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    derived = ResponseCache(Path(config.cache.dir) if Path(config.cache.dir).is_absolute()
                            else REPO_ROOT / config.cache.dir, "read_write")
    out_dir = run_dir / "inputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    built = []
    for doc, n in pages:
        path = document(doc)
        raw, ref, version, ws = engine_reading(doc, n)
        with pymupdf.open(path) as src:
            page = src[n - 1]
            png, width, height = scan.render_page_at(src, n, DPI)
            scale = DPI / 72.0
            units, tables = engine_units(page, n, raw, ref, version, scale)
            record = {
                "page_id": page_id(doc, n), "document": doc, "page": n, "unit": "K",
                "input_sha256": _sha256(path.read_bytes()),
                "image": {"file": f"{page_id(doc, n)}.png", "dpi": DPI, "width": width, "height": height,
                          "rotation": page.rotation, "size_pt": [round(page.rect.width, 2), round(page.rect.height, 2)]},
                "lines": units, "tables": tables, "engine": None,
                "engine_source": {"workspace": str(ws.relative_to(REPO_ROOT)), "raw_ref": ref, "engine": version},
                "local": local_lines(ws, n, page, scale),
                "regions": detector_regions(page, config, derived),
            }
        (out_dir / record["image"]["file"]).write_bytes(png)
        (out_dir / f"{record['page_id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        built.append(record)
        labels = {}
        for u in units:
            labels[u["label"]] = labels.get(u["label"], 0) + 1
        print(f"{record['page_id']}: {len(units)} engine blocks {labels}, {len(tables)} tables, "
              f"{len(record['local'])} local lines, {len(record['regions'])} regions")
    return built


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.run_dir)


if __name__ == "__main__":
    main()
