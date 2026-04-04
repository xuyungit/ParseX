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


from parserx.builders.ocr import html_table_to_markdown as _html_table_to_markdown


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
    import fitz

    doc = fitz.open()
    img = fitz.open(str(image_path))
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


def main():
    parser = argparse.ArgumentParser(
        description="Download OmniDocBench subset for evaluation"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("ground_truth_public"),
        help="Output directory (default: ground_truth_public/)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    names = setup_benchmark(args.output_dir)
    print(f"\nBenchmark ready: {len(names)} documents in {args.output_dir}/")
    for n in names:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
