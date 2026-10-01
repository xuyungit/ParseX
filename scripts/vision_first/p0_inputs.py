"""P0 probe inputs (execution plan §3.1): what the service model is given for one page.

- **Page image**: the page as shown, rendered at ``DPI`` (``content/scan.render_page_at``); every box below is in
  its pixels, turned with the page's /Rotate like every render (``ir/rotation.py``: PyMuPDF's rotation matrix).
- **Text layer, line by line**: the lines of the native extraction (``content/pdf_native._lines``, the same lines
  and the same order its ledger items number), each with its box, main size and face, and its text as the pipeline
  reads it (the copyable original).  Facts only, no inference: ``runs`` lists the line's spans with their own size,
  face and baseline (relative to the line's main baseline, points, positive = lower) when any of them differs from
  the main run; ``odd`` lists glyphs the text layer does not map to a readable character (private use, U+FFFD,
  bracket and integral pieces).
- **Script candidates**, apart from the text: glyphs set smaller than the glyph beside them *and* with their
  baseline shifted (the size-and-baseline reading of the content-by-region branch without its "one size smaller
  counts" condition, which made the false subscripts).  The model may accept or correct them.
- **Scan engine entries** (formula documents only): the engine's whole-page reading, the entries with mathematics
  (box and LaTeX).  Taken from the frozen run R's response cache (the same engine, model and options as M's;
  M read only some of these pages) and copied into the P0 run's own cache.
- **Layout detector regions**: hints.

Not shown to the model, kept for rendering its answer: the native extraction's tables (grids and the lines they
hold), so a ``table`` part is rendered with the existing grid.

    uv run python scripts/vision_first/p0_inputs.py --run-dir eval_runs/<run>
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import pymupdf  # noqa: E402

from parserx.cache import ResponseCache  # noqa: E402
from parserx.config.schema import load_config  # noqa: E402
from parserx.content import scan  # noqa: E402
from parserx.content.pdf_native import _is_cjk_or_fullwidth_punct, _line_typography, _lines, extract_pdf  # noqa: E402
from parserx.content.text import normalize_fullwidth_ascii, unify_radicals  # noqa: E402
from parserx.ir.enums import BlockKind, PageStatus  # noqa: E402
from parserx.ir.state import PageState  # noqa: E402
from parserx.layout.detector import RapidLayoutDetector, detect_cached  # noqa: E402
from parserx.scheduling import ServiceGateway  # noqa: E402
from parserx.scheduling.meter import RequestMeter  # noqa: E402
from parserx.services.ocr import PaddleOCRService  # noqa: E402

DPI = 150  # the page render the model sees: the tools' read resolution (``tools.read_dpi``)
GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public", REPO_ROOT / "ground_truth_unseen")  # unseen: new documents

# The ten pages (Q140).
PAGES: tuple[tuple[str, int], ...] = (
    ("paper_chn01", 2), ("paper_chn01", 3), ("paper_chn01", 7),
    ("paper_chn02", 1), ("paper_chn02", 3), ("paper_chn02", 4),
    ("ocr01", 1), ("receipt", 2),
    ("paper_chn02", 5), ("pdf_text01_tables", 1),
)
MATH_LABELS = frozenset({"display_formula", "inline_formula", "formula", "formula_number"})

# Script candidates (size and baseline only): a glyph set at most SCRIPT_SIZE of the glyph it is attached to, with
# its baseline SCRIPT_SHIFT of that glyph's size above or below it (the branch's measured constants).  Measurement
# conventions of typesetting, not of a document: a script stands at most ROW_SHIFT of the larger size off its base's
# baseline (sub- and superscripts shift about 0.15-0.45 em; the next row is a line spacing, over 1 em, away), and it
# is attached — nothing but smaller glyphs between them, at most ATTACHED of the base's size apart.
SCRIPT_SIZE = 0.85
SCRIPT_SHIFT = 0.12
ROW_SHIFT = 0.6
ATTACHED = 0.5
# Candidates version 2 (2026-09-30, eval_reports/2026-09-30_script_candidates.md §4): two lines one over the other
# are not a row; a glyph attached to a script is judged against that script's base; a prime is a character unless it
# stands inside a script.  The frozen runs keep the inputs they were built with (``inputs/``).
PRIMES = frozenset("′″‴⁗'")


def document(name: str) -> Path:
    """The document's source: its PDF, else its Word file (V sends only PDF pages; a Word file goes through the
    pipeline alone)."""
    for suffix in ("pdf", "docx", "doc"):
        for root in GT_DIRS:
            if (root / name / f"input.{suffix}").exists():
                return root / name / f"input.{suffix}"
    raise FileNotFoundError(name)


def page_id(doc: str, n: int) -> str:
    return f"{doc}_p{n}"


# ── the text layer ──────────────────────────────────────────────────────


def odd(ch: str) -> bool:
    """A glyph the text layer does not map to a readable character: private use, U+FFFD, a piece of a bracket or
    integral drawn from parts, or a control character."""
    cp = ord(ch)
    return (0xE000 <= cp <= 0xF8FF or cp >= 0xF0000 or cp == 0xFFFD or 0x239B <= cp <= 0x23B3
            or cp in (0x2320, 0x2321) or (unicodedata.category(ch) == "Cc" and ch not in "\t\n"))


def _face(span: dict) -> str:
    font = span.get("font", "")
    return font.split("+", 1)[1] if len(font) > 7 and font[6] == "+" else font


@dataclass
class RawLine:
    spans: list[dict]
    direction: tuple[float, float]


def raw_lines(page: pymupdf.Page) -> list[RawLine]:
    """The rawdict lines ``_lines`` keeps (non-blank), in its order, with their spans."""
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    out = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if "".join(ch.get("c", "") for s in spans for ch in s.get("chars", ())).strip():
                out.append(RawLine(spans, tuple(line.get("dir", (1.0, 0.0)))))
    return out


def _main_span(spans: list[dict], typography: tuple[float, bool, str]) -> dict:
    size, _bold, font = typography
    return next((s for s in spans if s.get("font", "") == font and round(s.get("size", 0.0), 1) == size
                 and s.get("chars")), next(s for s in spans if s.get("chars")))


def _baseline(span: dict) -> float:
    return span["chars"][0]["origin"][1]


def line_records(page: pymupdf.Page, scale: float) -> list[dict]:
    lines, raws = _lines(page), raw_lines(page)
    if len(lines) != len(raws):
        raise RuntimeError(f"page {page.number + 1}: {len(lines)} lines against {len(raws)} raw lines")
    shown = page.rotation_matrix
    records = []
    for seq, (line, raw) in enumerate(zip(lines, raws), 1):
        typography = _line_typography(raw.spans)
        main = _main_span(raw.spans, typography)
        base = _baseline(main)
        runs, glyphs_odd = [], []
        for span in raw.spans:
            chars = span.get("chars", ())
            if not chars:
                continue
            text = "".join(ch.get("c", "") for ch in chars)
            runs.append({"t": text, "size": round(span.get("size", 0.0), 1), "font": _face(span),
                         "base": round(_baseline(span) - base, 1)})
            glyphs_odd += [{"char": f"U+{ord(ch['c']):04X}", "font": _face(span)} for ch in chars
                           if ch.get("c") and odd(ch["c"])]
        record = {"id": f"L{seq}", "box": _px(pymupdf.Rect(line.bbox) * shown, scale),
                  "size": typography[0], "font": typography[2], "text": line.text,
                  "_blk": line.block}  # "_" keys are not shown to the model
        if glyphs_odd or any(r["size"] != typography[0] or r["base"] != 0.0 for r in runs if r["t"].strip()):
            record["runs"] = [r for r in runs if r["t"].strip()]
        if glyphs_odd:
            record["odd"] = glyphs_odd
        records.append(record)
    found_runs, glyph_kinds = script_candidates(raws)
    for record, raw, found, kinds in zip(records, raws, found_runs, glyph_kinds):
        if found:
            record["scripts"] = found
            record["_scripted"] = scripted_text(raw, kinds)  # what copy with "scripts": true places
    return records


def _px(rect: pymupdf.Rect, scale: float) -> list[int]:
    return [round(rect.x0 * scale), round(rect.y0 * scale), round(rect.x1 * scale), round(rect.y1 * scale)]


def script_candidates(raws: list[RawLine]) -> tuple[list[list[dict]], list[list[str]]]:
    """Per line: its runs of glyphs set smaller than the glyph they are attached to, with their baseline shifted
    ({"base": that glyph, "t": the run, "kind": "sup" | "sub"}), judged in the visual row — the horizontal lines
    overlapping it whose main baseline is within ``ROW_SHIFT`` — because the text layer often keeps a raised or
    lowered glyph as a line of its own.  Also, per line, the kind of each of its glyphs ("sup", "sub" or "")."""
    glyphs, mains = [], []  # per line: [(char, x0, x1, size, baseline)], (main baseline, main size)
    glyph_kinds: list[list[str]] = []
    for raw in raws:
        horizontal = abs(raw.direction[0] - 1.0) < 0.01 and abs(raw.direction[1]) < 0.01
        gs = [(ch.get("c", ""), ch["bbox"][0], ch["bbox"][2], span.get("size", 0.0), ch["origin"][1],
               ch["bbox"][1], ch["bbox"][3]) for span in raw.spans for ch in span.get("chars", ())] if horizontal else []
        glyphs.append(gs)
        main = _main_span(raw.spans, _line_typography(raw.spans)) if gs else None
        mains.append((_baseline(main), main.get("size", 0.0), min(g[5] for g in gs), max(g[6] for g in gs))
                     if main else None)

    def same_row(i: int, j: int) -> bool:
        a, b = mains[i], mains[j]
        return (a is not None and b is not None and min(a[3], b[3]) > max(a[2], b[2])
                and abs(a[0] - b[0]) <= ROW_SHIFT * max(a[1], b[1]) and (i == j or not stacked(i, j)))

    def stacked(i: int, j: int) -> bool:
        """One line over the other: one line's extent holds a whole glyph of the other set larger than its own
        glyphs (a caption's second language, an affiliation under the names, a table head's second line, the parts
        of a fraction).  A script stands beside its base — kerned into it at most, never spanning a larger glyph —
        and nominal sizes differ between fonts, so a size-based shift alone cannot tell."""
        for a, b in ((i, j), (j, i)):
            own = [g for g in glyphs[a] if g[0].strip()]
            if not own:
                continue
            left, right, small = min(g[1] for g in own), max(g[2] for g in own), min(g[3] for g in own)
            if any(h[0].strip() and h[3] * SCRIPT_SIZE >= small and left <= h[1] and h[2] <= right for h in glyphs[b]):
                return True
        return False

    out: list[list[dict]] = []
    for i, own in enumerate(glyphs):
        if not own:
            out.append([])
            glyph_kinds.append([])
            continue
        row = sorted(((g[:5], j == i, k, j) for j, gs in enumerate(glyphs) if gs and same_row(i, j)
                      for k, g in enumerate(gs)), key=lambda item: (item[0][1], item[0][2]))
        kinds = _kinds([g for g, _, _, _ in row], [j for _, _, _, j in row], same_row)
        found: list[dict] = []
        own_kinds = [""] * len(own)
        previous = None
        for (g, mine, k, _), (kind, base) in zip(row, kinds):
            if mine:
                own_kinds[k] = kind
            if not mine or not kind:
                previous = None
                continue
            if found and previous == (kind, base):
                found[-1]["t"] += g[0]
            else:
                found.append({"base": base, "t": g[0], "kind": kind})
            previous = (kind, base)
        out.append([f for f in found if f["t"].strip()])
        glyph_kinds.append(own_kinds)
    return out, glyph_kinds


def scripted_text(raw: RawLine, kinds: list[str]) -> str:
    """The line as the pipeline reads it (``pdf_native._reconstruct_line_from_chars``: spaces at gaps, none between
    wide characters; radicals unified, full width folded), its candidate scripts written ``<sup>…</sup>`` /
    ``<sub>…</sub>`` — what a ``copy`` part taking the candidates places."""
    chars = [(unify_radicals(ch.get("c", "")), ch["bbox"][0], ch["bbox"][2], span.get("size", 12.0), kind)
             for (span, ch), kind in zip(((span, ch) for span in raw.spans for ch in span.get("chars", ())), kinds)]
    chars = [c for c in chars if c[0]]
    pieces: list[tuple[str, str]] = []
    for n, (c, x0, _x1, size, kind) in enumerate(chars):
        if n:
            prev = chars[n - 1]
            if x0 - prev[2] > (prev[3] + size) * 0.125 and not (_is_cjk_or_fullwidth_punct(prev[0])
                                                                and _is_cjk_or_fullwidth_punct(c)):
                pieces.append((" ", kind if kind and kind == prev[4] else ""))
        pieces.append((c, kind))
    out, n = [], 0
    while n < len(pieces):
        kind = pieces[n][1]
        m = n
        while m < len(pieces) and pieces[m][1] == kind:
            m += 1
        text = normalize_fullwidth_ascii("".join(p[0] for p in pieces[n:m]))
        out.append(f"<{kind}>{text.strip()}</{kind}>" if kind else text)
        n = m
    return "".join(out).strip()


def _kinds(glyphs: list[tuple], lines: list[int], same_row) -> list[tuple[str, str]]:
    """For each glyph (char, x0, x1, size, baseline) of a row, in order along it: ("sup" | "sub" | "", the glyph
    it is a script of).  Its base is the nearest glyph set larger (to its left, else to its right) with nothing
    but smaller glyphs between them, at most ``ATTACHED`` of the base's size apart, and those glyphs in the base's
    row too (a line's row holds the lines beside it, which need not be beside each other: a reference in the right
    column is not a script of a heading in the left one across the body line between); when that glyph is itself a
    script, the glyph is judged against that script's base too: level with the script, it is part of it (the digits of
    a citation beside its raised brackets); level with the base, it is none (the full stop after the citation);
    shifted from both, a script of the script.  A prime is a script only beside one, and then part of it."""
    marks = [k for k, g in enumerate(glyphs) if g[0].strip()]
    out = [("", "")] * len(glyphs)
    base_of: dict[int, int] = {}
    for at, i in enumerate(marks):
        ch, x0, x1, size, baseline = glyphs[i]
        base = None
        for step in (-1, 1):
            k = at + step
            while 0 <= k < len(marks) and glyphs[marks[k]][3] * SCRIPT_SIZE < size:  # not set larger: not a base
                k += step
            if 0 <= k < len(marks):
                j = marks[k]
                run = marks[min(at, k) + 1:max(at, k)] + [i]
                near = min(glyphs[r][1] for r in run) if step < 0 else max(glyphs[r][2] for r in run)
                gap = near - glyphs[j][2] if step < 0 else glyphs[j][1] - near
                if gap <= ATTACHED * glyphs[j][3] and all(same_row(lines[r], lines[j]) for r in run):
                    base = j
                    break
        if base is None:
            continue
        base_of[i] = base
        out[i] = _shifted(glyphs[i], glyphs[base])
    first = {i: out[i] for i in base_of}
    for i, j in base_of.items():  # a glyph attached to a script, against that script's own base:
        if not first.get(j, ("", ""))[0] or j not in base_of:
            continue
        if not _shifted(glyphs[i], glyphs[base_of[j]])[0]:
            out[i] = ("", "")  # back on the base's line (the full stop after a raised citation)
        elif not first[i][0]:
            out[i] = first[j]  # level with the script: part of it (a citation's digits beside its brackets)
        # else shifted from the script too: a script of the script (φ_j raised), as judged
    for at, i in enumerate(marks):  # a prime is a script only beside one (x′ in q^{x′}), and then part of it
        if glyphs[i][0] in PRIMES:
            near = [out[marks[k]] for k in (at - 1, at + 1)
                    if 0 <= k < len(marks) and glyphs[marks[k]][0] not in PRIMES and out[marks[k]][0]]
            out[i] = near[0] if near else ("", "")
    return out


def _shifted(glyph: tuple, base: tuple) -> tuple[str, str]:
    shift = (glyph[4] - base[4]) / base[3]
    return ("sup" if shift < 0 else "sub", base[0]) if abs(shift) >= SCRIPT_SHIFT else ("", "")


# ── the scan engine, the detector, the extraction ───────────────────────


def engine_pages(doc: str, formula_pages: list[int], cache: Path, config) -> dict[int, tuple[str, dict]]:
    """page → (cache key, the engine's page result) for every formula page of the document — the native pages the
    pipeline's formula step reads, in its batches (``tools/formulas.read_formula_pages``: ``tools.scan_batch_pages``
    at a time), so a recorded reading replays — through *cache* (read-write: a page no record holds is read now).
    The same for every document with formulas (round-2 plan: no document is given more than another)."""
    ocr = PaddleOCRService(config.builders.ocr)
    ocr.gateway = ServiceGateway(RequestMeter(), ResponseCache(cache, "read_write"))
    size = config.tools.scan_batch_pages
    out: dict[int, tuple[str, dict]] = {}
    with pymupdf.open(document(doc)) as src:
        for i in range(0, len(formula_pages), size):
            batch = formula_pages[i:i + size]
            data = scan.batch_pdf(src, batch)
            key = ocr.request_key(data, "application/pdf")
            for n, result in zip(batch, ocr.recognize_pdf(data)):
                out[n] = (key, result.raw["layoutParsingResults"][0])
    return out


def engine_entries(page: pymupdf.Page, n: int, reading: tuple[str, dict], scale: float, model: str) -> list[dict]:
    key, raw = reading
    state = PageState(n=n, unit="pdf_page", status=PageStatus.DONE, size_pt=(page.rect.width, page.rect.height), rotation=page.rotation)
    result = scan.page_blocks(scan.PageScan(page=n, raw=raw, raw_ref=key, engine_version=model),
                              page_size=(page.rect.width, page.rect.height), first_seq=1, first_item=1, page=state)
    entries = []
    for block in result.blocks:
        obs = block.observations[0]
        text = block.text or ""
        if block.kind == BlockKind.FORMULA:
            text = f"$${text}$$" if not text.strip().startswith("$") else text
        if obs.label not in MATH_LABELS and "$" not in text and "\\" not in text:
            continue
        entries.append({"id": f"E{len(entries) + 1}", "label": obs.label,
                        "box": _px(pymupdf.Rect(block.anchors[0].bbox) * page.rotation_matrix, scale),
                        "text": text})
    return entries


def detector_regions(page: pymupdf.Page, config, cache) -> list[dict]:
    detector = RapidLayoutDetector(config.layout.model, config.layout.conf_thresh, layout=config.layout)
    dpi = config.layout.page_dpi
    png = page.get_pixmap(dpi=dpi).tobytes("png")
    k = DPI / dpi  # the detector's render and ours both show the page as shown
    return [{"label": r.label, "box": [round(v * k) for v in r.bbox]} for r in detect_cached(detector, png, cache)]


def page_layout(config, cache):
    """The layout callable of the native extraction (``tools/init._page_layout``)."""
    detector = RapidLayoutDetector(config.layout.model, config.layout.conf_thresh, layout=config.layout)
    dpi = config.layout.page_dpi

    def regions(page):
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        k, back = 72.0 / dpi, page.derotation_matrix
        return [(r.label, tuple(pymupdf.Rect(*(v * k for v in r.bbox)) * back))
                for r in detect_cached(detector, png, cache)]

    return regions


def extraction_tables(doc: str, config, cache) -> dict[int, list[dict]]:
    """page → the native extraction's tables: their grid (HTML) and the line numbers (``L<seq>``) they hold."""
    ext = extract_pdf(document(doc), layout=page_layout(config, cache), check_tables=config.layout.check_tables)
    lines_of: dict[str, list[str]] = {}
    for entry in ext.ledger:
        if entry.unit == "native_line" and entry.block:
            lines_of.setdefault(entry.block, []).append(entry.item)
    out: dict[int, list[dict]] = {}
    for block in ext.blocks:
        if block.kind != BlockKind.TABLE or block.cells is None:
            continue
        n = block.anchors[0].page
        seqs = sorted(int(item.rsplit("-", 1)[1]) for item in lines_of.get(block.id, []))
        out.setdefault(n, []).append({"block": block.id, "lines": [f"L{s}" for s in seqs], "html": block.cells.to_html()})
    return out


# ── one page ────────────────────────────────────────────────────────────


def build(run_dir: Path, pages=PAGES, engine: dict | None = None) -> list[dict]:
    """*engine*: ``{"cache": path, "pages": {document: its formula pages}}`` — the scan engine's readings of the
    formula pages (``engine_pages``); without it, no page has engine entries."""
    config = load_config(REPO_ROOT / "configs" / "regression.yaml")
    derived = ResponseCache(Path(config.cache.dir) if Path(config.cache.dir).is_absolute()
                            else REPO_ROOT / config.cache.dir, "read_write")
    out_dir = run_dir / "inputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    tables: dict[str, dict] = {}
    engines: dict[str, dict] = {}
    built = []
    for doc, n in pages:
        path = document(doc)
        if doc not in tables:
            tables[doc] = extraction_tables(doc, config, derived)
        if engine and doc not in engines:
            formula_pages = sorted(engine["pages"].get(doc) or [])
            engines[doc] = engine_pages(doc, formula_pages, engine["cache"], config) if formula_pages else {}
        with pymupdf.open(path) as src:
            page = src[n - 1]
            png, width, height = scan.render_page_at(src, n, DPI)
            scale = DPI / 72.0
            record = {
                "page_id": page_id(doc, n), "document": doc, "page": n,
                "input_sha256": _sha256(path.read_bytes()),
                "image": {"file": f"{page_id(doc, n)}.png", "dpi": DPI, "width": width, "height": height,
                          "rotation": page.rotation, "size_pt": [round(page.rect.width, 2), round(page.rect.height, 2)]},
                "lines": line_records(page, scale),
                "engine": (engine_entries(page, n, engines[doc][n], scale, config.builders.ocr.model)
                           if doc in engines and n in engines[doc] else None),
                "engine_source": ({"cache_key": engines[doc][n][0], "formula_pages": sorted(engine["pages"][doc])}
                                  if doc in engines and n in engines[doc] else None),
                "regions": detector_regions(page, config, derived),
                "tables": tables[doc].get(n, []),
            }
        (out_dir / record["image"]["file"]).write_bytes(png)
        (out_dir / f"{record['page_id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        built.append(record)
        print(f"{record['page_id']}: {len(record['lines'])} lines, "
              f"{sum('runs' in r for r in record['lines'])} with runs, {sum('odd' in r for r in record['lines'])} odd, "
              f"{sum(len(r.get('scripts', [])) for r in record['lines'])} script candidates, "
              f"{len(record['engine'] or [])} engine entries, {len(record['regions'])} regions, "
              f"{len(record['tables'])} tables")
    return built


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def load(run_dir: Path, pid: str) -> dict:
    return json.loads((run_dir / "inputs" / f"{pid}.json").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.run_dir)


if __name__ == "__main__":
    main()
