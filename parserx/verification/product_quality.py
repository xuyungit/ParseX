"""Semi-automatic product-quality checks for rendered Markdown output.

These checks catch issues that core fidelity metrics miss: leaked
internal placeholders, HTML table markup in Markdown-first output,
broken image asset links, and duplicated body text from OCR overlap.
"""

from __future__ import annotations

import re
from pathlib import Path

from parserx.models.elements import Document
from parserx.text_utils import normalize_for_comparison

_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_HTML_TABLE_RE = re.compile(r"<table[\s>]", re.IGNORECASE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Fragments that must never appear in user-facing Markdown.  This is a
# generic safety net — if any new internal marker leaks, add it here.
_INTERNAL_MARKER_FRAGMENTS = (
    "preserved in OCR body text",
    "preserved in body text",
)

# Metadata key names that should only exist in element metadata, never
# in the rendered text body.
_METADATA_LEAK_RE = re.compile(
    r"\b(?:skip_render|description_source|vlm_skipped_due_to)\b",
)


class ProductQualityChecker:
    """Check rendered Markdown for product-quality regressions."""

    def check(
        self,
        doc: Document,
        markdown: str,
        output_dir: Path | None = None,
    ) -> list[str]:
        warnings: list[str] = []
        warnings.extend(self._check_placeholder_leakage(markdown))
        warnings.extend(self._check_html_table_leakage(markdown))
        warnings.extend(self._check_image_asset_linkage(markdown, output_dir))
        warnings.extend(self._check_duplicate_body_text(doc))
        return warnings

    # ------------------------------------------------------------------
    # Check 1: Placeholder / debug text leakage
    # ------------------------------------------------------------------

    def _check_placeholder_leakage(self, markdown: str) -> list[str]:
        """Detect internal marker strings that leaked into output."""
        warnings: list[str] = []
        # Strip HTML comments so we don't flag page markers etc.
        body = _HTML_COMMENT_RE.sub("", markdown)

        for frag in _INTERNAL_MARKER_FRAGMENTS:
            if frag in body:
                warnings.append(
                    f"Placeholder/debug text detected in output: '{frag}'"
                )

        match = _METADATA_LEAK_RE.search(body)
        if match:
            warnings.append(
                f"Placeholder/debug text detected in output: '{match.group()}'"
            )

        return warnings

    # ------------------------------------------------------------------
    # Check 2: HTML table leakage
    # ------------------------------------------------------------------

    def _check_html_table_leakage(self, markdown: str) -> list[str]:
        """Detect raw HTML <table> markup in Markdown-first output."""
        body = _HTML_COMMENT_RE.sub("", markdown)
        matches = _HTML_TABLE_RE.findall(body)
        if not matches:
            return []
        return [
            f"HTML table markup detected in output ({len(matches)} occurrence(s))."
        ]

    # ------------------------------------------------------------------
    # Check 3: Image asset linkage
    # ------------------------------------------------------------------

    def _check_image_asset_linkage(
        self, markdown: str, output_dir: Path | None,
    ) -> list[str]:
        """Verify Markdown image references and disk files are consistent."""
        if output_dir is None:
            return []

        warnings: list[str] = []

        # Markdown → disk
        referenced_paths: set[str] = set()
        for match in _IMAGE_REF_RE.finditer(markdown):
            rel_path = match.group(1)
            referenced_paths.add(rel_path)
            full = output_dir / rel_path
            if not full.exists():
                warnings.append(
                    f"Image reference '{rel_path}' in Markdown but file not found on disk."
                )

        # Disk → Markdown
        images_dir = output_dir / "images"
        if images_dir.is_dir():
            for f in sorted(images_dir.iterdir()):
                if f.is_file():
                    rel = f"images/{f.name}"
                    if rel not in referenced_paths:
                        warnings.append(
                            f"Image file '{f.name}' on disk but not referenced in Markdown."
                        )

        return warnings

    # ------------------------------------------------------------------
    # Check 4: Duplicate body text (image description ≈ nearby body)
    # ------------------------------------------------------------------

    def _check_duplicate_body_text(self, doc: Document) -> list[str]:
        """Detect image descriptions that duplicate nearby body text."""
        warnings: list[str] = []

        for page in doc.pages:
            images = [
                e for e in page.elements
                if e.type == "image"
                and e.metadata.get("text_heavy_image")
                and e.metadata.get("description", "").strip()
                and not e.metadata.get("skipped")
            ]
            if not images:
                continue

            text_elements = [
                e for e in page.elements
                if e.type in ("text", "table") and e.content.strip()
            ]
            if not text_elements:
                continue

            page_body = normalize_for_comparison(
                " ".join(e.content for e in text_elements)
            )
            if not page_body:
                continue

            for img in images:
                desc = normalize_for_comparison(
                    str(img.metadata.get("description", ""))
                )
                if not desc or len(desc) < 20:
                    continue
                overlap = _char_overlap_ratio(desc, page_body)
                if overlap > 0.6:
                    warnings.append(
                        f"Page {page.number}: image description duplicates "
                        f"nearby body text ({overlap:.0%} overlap)."
                    )

        return warnings


def _char_overlap_ratio(short: str, long: str) -> float:
    """Fraction of characters in *short* that also appear in *long*.

    Uses character-frequency comparison — fast and sufficient for
    detecting near-duplicate text without expensive alignment.
    """
    if not short:
        return 0.0
    from collections import Counter

    short_freq = Counter(short)
    long_freq = Counter(long)
    common = sum(min(short_freq[c], long_freq[c]) for c in short_freq)
    return common / len(short)
