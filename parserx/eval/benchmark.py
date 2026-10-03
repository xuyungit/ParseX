"""Download and prepare OmniDocBench subset for public evaluation.

OmniDocBench (opendatalab/OmniDocBench on HuggingFace) is a standard
document parsing benchmark with 1355 pages across 9 document types,
Chinese + English.

This module downloads a curated subset, converts page images to
single-page PDFs, and generates expected.md ground truth files in
the standard ground_truth/ layout that EvalRunner understands.

Usage:
    uv run python -m parserx.eval.benchmark [--output-dir ground_truth_public]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

DATASET_REPO = "opendatalab/OmniDocBench"
ANNOTATION_FILE = "OmniDocBench.json"

# Curated subset: (index, short_name, reason)
# Selected for diversity: language, doc type, with/without tables,
# moderate complexity (not 100-element newspaper pages).
SUBSET_SPEC = [
    # Chinese text — book / research report
    {"source": "book", "lang": "simplified_chinese", "need_table": False, "n": 2},
    # English text — academic / book
    {"source": "academic_literature", "lang": "english", "need_table": False, "n": 2},
    # Chinese table — research report / exam
    {"source": "research_report", "lang": "simplified_chinese", "need_table": True, "n": 2},
    # English table — academic
    {"source": "academic_literature", "lang": "english", "need_table": True, "n": 2},
]


def _select_pages(data: list[dict]) -> list[tuple[int, dict, str]]:
    """Select pages from OmniDocBench matching SUBSET_SPEC.

    Returns list of (index, page_data, short_name).
    """
    selected: list[tuple[int, dict, str]] = []
    used_indices: set[int] = set()

    for spec in SUBSET_SPEC:
        candidates = []
        for i, page in enumerate(data):
            if i in used_indices:
                continue
            pa = page["page_info"]["page_attribute"]
            if pa["data_source"] != spec["source"]:
                continue
            if pa["language"] != spec["lang"]:
                continue
            cats = [d["category_type"] for d in page["layout_dets"]]
            has_table = "table" in cats
            if has_table != spec["need_table"]:
                continue
            # Prefer moderate complexity (5-30 elements)
            n_elem = len(page["layout_dets"])
            if n_elem < 4 or n_elem > 35:
                continue
            # Compute text richness for sorting
            text_len = sum(
                len(d.get("text", "") or "")
                for d in page["layout_dets"]
                if d["category_type"] in ("text_block", "title")
            )
            if text_len < 100:
                continue
            candidates.append((i, page, text_len))

        # Pick top N by text length
        candidates.sort(key=lambda x: -x[2])
        lang_short = "zh" if "chinese" in spec["lang"] else "en"
        table_tag = "table" if spec["need_table"] else "text"
        for rank, (idx, page, _) in enumerate(candidates[: spec["n"]]):
            name = f"omnidoc_{spec['source']}_{lang_short}_{table_tag}_{rank + 1:02d}"
            selected.append((idx, page, name))
            used_indices.add(idx)

    return selected


# ── Ground truth conversion ────────────────────────────────────────────


from parserx.tables.html import html_table_to_markdown as _html_table_to_markdown


def _page_to_expected_md(page: dict) -> str:
    """Convert OmniDocBench page annotations to expected Markdown."""
    elements = sorted(page["layout_dets"], key=lambda d: d.get("order") or 0)
    parts: list[str] = []

    for elem in elements:
        cat = elem["category_type"]
        text = (elem.get("text") or "").strip()

        if cat == "title" and text:
            parts.append(f"## {text}")
        elif cat == "text_block" and text:
            parts.append(text)
        elif cat == "table":
            html = elem.get("html", "")
            if html:
                md_table = _html_table_to_markdown(html)
                if md_table:
                    parts.append(md_table)
        elif cat == "table_caption" and text:
            parts.append(f"**{text}**")
        elif cat == "figure_caption" and text:
            parts.append(f"*{text}*")
        elif cat == "equation_isolated":
            latex = (elem.get("latex") or "").strip()
            if latex:
                parts.append(f"$${latex}$$")
        # Skip: header, footer, page_number, abandon, figure, etc.

    return "\n\n".join(parts) + "\n"


# ── Image → PDF conversion ────────────────────────────────────────────


def _image_to_pdf(image_path: Path, pdf_path: Path) -> None:
    """Convert a page image to a single-page PDF using PyMuPDF."""
    import pymupdf

    doc = pymupdf.open()
    img = pymupdf.open(str(image_path))
    # Get image dimensions
    page = img[0]
    rect = page.rect
    # Create PDF page with same dimensions
    pdf_page = doc.new_page(width=rect.width, height=rect.height)
    pdf_page.insert_image(rect, filename=str(image_path))
    doc.save(str(pdf_path))
    doc.close()
    img.close()


# ── Main setup ─────────────────────────────────────────────────────────


def setup_benchmark(output_dir: Path) -> list[str]:
    """Download OmniDocBench subset and prepare ground truth directory.

    Returns list of created document names.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise RuntimeError(
            "Install bench dependencies: uv pip install 'parserx[bench]'"
        )

    log.info("Downloading OmniDocBench annotations...")
    json_path = hf_hub_download(DATASET_REPO, ANNOTATION_FILE, repo_type="dataset")
    with open(json_path) as f:
        data = json.load(f)

    log.info("Loaded %d pages, selecting subset...", len(data))
    selected = _select_pages(data)
    log.info("Selected %d pages for benchmark", len(selected))

    created = []
    for idx, page, name in selected:
        doc_dir = output_dir / name
        doc_dir.mkdir(parents=True, exist_ok=True)

        # Download page image
        image_rel = page["page_info"]["image_path"]
        image_hf = f"images/{image_rel}"
        log.info("  %s: downloading %s", name, image_rel)
        local_image = Path(
            hf_hub_download(DATASET_REPO, image_hf, repo_type="dataset")
        )

        # Convert image → single-page PDF
        pdf_path = doc_dir / "input.pdf"
        _image_to_pdf(local_image, pdf_path)

        # Generate expected.md from annotations
        expected_md = _page_to_expected_md(page)
        (doc_dir / "expected.md").write_text(expected_md, encoding="utf-8")

        # Save source metadata for traceability
        meta = {
            "source": "OmniDocBench",
            "omnidoc_index": idx,
            "image_path": image_rel,
            "page_attribute": page["page_info"]["page_attribute"],
        }
        (doc_dir / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        created.append(name)
        log.info("  %s: created (PDF + expected.md)", name)

    return created


# ── OCR engine comparison (docs/v2_ocr_engines.md) ─────────────────────

# The kinds of page our own corpus lacks, one or two typical pages each, plus page conditions that make reading
# hard.  "Typical": the page whose text length is nearest the median of its kind, so the choice follows the data,
# not what any engine reads well.
OCR_SUBSET_SPEC = [
    {"source": "exam_paper", "lang": "simplified_chinese", "n": 2},
    {"source": "exam_paper", "lang": "english", "n": 1},
    {"source": "exam_paper", "lang": "en_ch_mixed", "n": 1},
    {"source": "note", "lang": "simplified_chinese", "n": 2},
    {"source": "note", "lang": "en_ch_mixed", "n": 1},
    {"source": "newspaper", "lang": "simplified_chinese", "n": 1},
    {"source": "newspaper", "lang": "english", "n": 1},
    {"source": "PPT2PDF", "lang": "simplified_chinese", "n": 1},
    {"source": "PPT2PDF", "lang": "english", "n": 1},
    {"source": "colorful_textbook", "lang": "simplified_chinese", "n": 1},
    {"source": "colorful_textbook", "lang": "english", "n": 1},
    {"source": "magazine", "lang": "simplified_chinese", "n": 1},
    {"source": "magazine", "lang": "english", "n": 1},
    {"source": "book", "lang": "english", "n": 1},
    {"issue": "fuzzy_scan", "n": 2},
    {"issue": "table_with_formula", "n": 1},
    {"issue": "watermark", "n": 1},
]
_LANG_SHORT = {"simplified_chinese": "zh", "english": "en", "en_ch_mixed": "mixed"}
_MASKS = {"text_mask", "table_mask", "need_mask"}  # regions OmniDocBench leaves out of scoring: we cannot
_MAX_ELEMENTS = 120


def _select_ocr_pages(data: list[dict], exclude: set[str]) -> list[tuple[int, dict, str]]:
    """Pages for ``OCR_SUBSET_SPEC`` (not the images in *exclude*): unmasked, with some text, the nearest the
    median text length first."""
    selected: list[tuple[int, dict, str]] = []
    used = {i for i, page in enumerate(data) if page["page_info"]["image_path"] in exclude}
    for spec in OCR_SUBSET_SPEC:
        stratum = []
        for i, page in enumerate(data):
            pa = page["page_info"]["page_attribute"]
            if "issue" in spec:
                if spec["issue"] not in (pa.get("special_issue") or []):
                    continue
            elif (pa["data_source"], pa["language"]) != (spec["source"], spec["lang"]):
                continue
            dets = page["layout_dets"]
            if any(d["category_type"] in _MASKS for d in dets) or len(dets) > _MAX_ELEMENTS:
                continue
            length = sum(len(d.get("text") or "") + len(d.get("html") or "") for d in dets)
            if length >= 150:
                stratum.append((i, page, length))
        if not stratum:
            continue
        median = sorted(length for _, _, length in stratum)[len(stratum) // 2]
        stratum.sort(key=lambda c: (abs(c[2] - median), c[0]))
        picked = [c for c in stratum if c[0] not in used][: spec["n"]]
        for rank, (i, page, _) in enumerate(picked, 1):
            pa = page["page_info"]["page_attribute"]
            kind = spec.get("issue") or f"{spec['source'].lower()}_{_LANG_SHORT[spec['lang']]}"
            if "issue" in spec:
                kind += f"_{_LANG_SHORT.get(pa['language'], 'other')}"
            selected.append((i, page, f"omni_{kind}_{rank:02d}"))
            used.add(i)
    return selected


def _page_to_expected_md_full(page: dict) -> str:
    """Like ``_page_to_expected_md``, with the reading text it leaves out: references, footnotes, code."""
    parts: list[str] = []
    for elem in sorted(page["layout_dets"], key=lambda d: d.get("order") or 0):
        cat = elem["category_type"]
        text = (elem.get("text") or "").strip()
        if cat == "title" and text:
            parts.append(f"## {text}")
        elif cat in ("text_block", "reference", "page_footnote", "figure_footnote", "table_footnote") and text:
            parts.append(text)
        elif cat == "code_txt" and text:
            parts.append(f"```\n{text}\n```")
        elif cat == "table":
            md_table = _html_table_to_markdown(elem.get("html", "")) if elem.get("html") else ""
            if md_table:
                parts.append(md_table)
        elif cat == "table_caption" and text:
            parts.append(f"**{text}**")
        elif cat in ("figure_caption", "code_txt_caption") and text:
            parts.append(f"*{text}*")
        elif cat == "equation_isolated" and (elem.get("latex") or "").strip():
            parts.append(f"$${elem['latex'].strip()}$$")
    return "\n\n".join(parts) + "\n"


def setup_ocr_subset(output_dir: Path) -> list[str]:
    """Download the OCR comparison pages (``OCR_SUBSET_SPEC``) into *output_dir*; returns the document names."""
    from huggingface_hub import hf_hub_download

    json_path = hf_hub_download(DATASET_REPO, ANNOTATION_FILE, repo_type="dataset")
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    revision = Path(json_path).parent.name  # the dataset snapshot: pages and indices change between versions
    exclude = {page["page_info"]["image_path"] for _, page, _ in _select_pages(data)}  # ground_truth_public
    created = []
    for idx, page, name in _select_ocr_pages(data, exclude):
        doc_dir = output_dir / name
        doc_dir.mkdir(parents=True, exist_ok=True)
        image_rel = page["page_info"]["image_path"]
        local_image = Path(hf_hub_download(DATASET_REPO, f"images/{image_rel}", repo_type="dataset"))
        _image_to_pdf(local_image, doc_dir / "input.pdf")
        (doc_dir / "expected.md").write_text(_page_to_expected_md_full(page), encoding="utf-8")
        meta = {"source": "OmniDocBench", "revision": revision, "omnidoc_index": idx, "image_path": image_rel,
                "page_attribute": page["page_info"]["page_attribute"], "outline": False}
        (doc_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        created.append(name)
        log.info("  %s: %s", name, image_rel)
    return created


def main():
    parser = argparse.ArgumentParser(
        description="Download OmniDocBench subset for evaluation"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: ground_truth_public/, with --ocr ground_truth_ocr/)",
    )
    parser.add_argument("--ocr", action="store_true", help="the OCR engine comparison pages (OCR_SUBSET_SPEC)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.ocr:
        out = args.output_dir or Path("ground_truth_ocr")
        names = setup_ocr_subset(out)
        print(f"\nOCR comparison pages: {len(names)} documents in {out}/")
        return
    args.output_dir = args.output_dir or Path("ground_truth_public")
    names = setup_benchmark(args.output_dir)
    print(f"\nBenchmark ready: {len(names)} documents in {args.output_dir}/")
    for n in names:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
