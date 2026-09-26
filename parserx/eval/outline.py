"""Which documents' heading scores count (user decision Q79, 2026-09-26).

A very short document, or one without a real outline (a receipt, a one-page excerpt), has no heading structure
worth judging: whether it gets titles, and at which levels, is a matter of taste.  Its heading scores are still
computed and shown per document, but heading averages and regression checks count only documents with an outline:

- the annotation has at least ``MIN_HEADINGS`` headings on at least two levels, and
- a PDF has more than ``MAX_SHORT_PAGES`` pages;
- ``meta.json`` ``"outline": true | false`` overrides both (a receipt has two levels but no structure to speak of).

The two numbers are the user's description of "short" and "no hierarchy", not tuned on scores.
"""

from __future__ import annotations

import json
from pathlib import Path

MIN_HEADINGS = 3
MAX_SHORT_PAGES = 2


def has_outline(doc_dir: Path, expected_md: str | None = None) -> bool:
    doc_dir = Path(doc_dir)
    meta = doc_dir / "meta.json"
    if meta.is_file():
        declared = json.loads(meta.read_text(encoding="utf-8")).get("outline")
        if isinstance(declared, bool):
            return declared
    from parserx.eval.metrics import _extract_headings

    text = expected_md if expected_md is not None else (doc_dir / "expected.md").read_text(encoding="utf-8")
    headings = _extract_headings(text)
    if len(headings) < MIN_HEADINGS or len({level for level, _ in headings}) < 2:
        return False
    pdf = doc_dir / "input.pdf"
    if pdf.is_file():
        import fitz

        with fitz.open(pdf) as doc:
            if doc.page_count <= MAX_SHORT_PAGES:
                return False
    return True
