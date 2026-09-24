"""Native text layer quality (guide §6.3): does a PDF page's text layer carry its content?

v1's page-classification signals become evidence here.  A page whose native
layer fails goes to the scan engine; the verdict and its evidence are recorded
in a ``content_source`` Decision.  Thresholds are v1's, validated on the corpus
since Iter 12; they only decide whether recognition is worth a request.

Invisible text (PDF text render mode 3) is what searchable scans put over the
page image: the reader sees the raster, not that text.  A page whose text is
mostly invisible is therefore judged by its image (2026-09-24: across the 24
corpus PDFs only the scanned ocr_scan_jtg3362 has any invisible text, 100 %
on every page; its cover is tiled into 7 images, so v1's dominant-image test
alone let its erroneous text layer through).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSignals:
    page_area: float
    text_chars: int  # non-whitespace native characters
    replacement_chars: int  # U+FFFD: glyphs without a Unicode mapping
    private_use_chars: int  # U+E000–U+F8FF: font-private codes, not text
    image_coverage: float  # summed image area / page area
    dominant_image_ratio: float  # largest image area / page area
    chars_in_dominant_image: int  # native characters whose line centre lies inside the largest image
    drawings: int  # vector drawing operations
    invisible_chars: int = 0  # characters drawn with text render mode 3


@dataclass(frozen=True)
class NativeVerdict:
    ok: bool
    reason: str  # ok | no_text_layer | image_content | vector_text | ocr_text_layer | garbled
    evidence: dict[str, float | int]


def assess_native_layer(s: PageSignals) -> NativeVerdict:
    evidence: dict[str, float | int] = {
        "text_chars": s.text_chars,
        "image_coverage": round(s.image_coverage, 4),
        "dominant_image_ratio": round(s.dominant_image_ratio, 4),
        "drawings": s.drawings,
    }
    if s.text_chars and s.invisible_chars * 2 > s.text_chars:
        evidence["invisible_chars"] = s.invisible_chars
        return NativeVerdict(False, "ocr_text_layer", evidence)
    if s.text_chars < 50 and s.image_coverage > 0.5:
        return NativeVerdict(False, "no_text_layer", evidence)
    if s.text_chars < 200 and s.image_coverage > 0.3:
        # v1's "mixed page": the images carry what the page says (e.g. a scan placed with wide margins).
        return NativeVerdict(False, "image_content", evidence)
    if s.text_chars < 500 and s.drawings > 200:
        return NativeVerdict(False, "vector_text", evidence)
    if s.text_chars and s.dominant_image_ratio > 0.5 and s.chars_in_dominant_image / s.text_chars > 0.7:
        evidence["chars_in_dominant_image"] = s.chars_in_dominant_image
        return NativeVerdict(False, "ocr_text_layer", evidence)
    if s.text_chars:
        unmapped = (s.replacement_chars + s.private_use_chars) / s.text_chars
        evidence["unmapped_ratio"] = round(unmapped, 4)
        if unmapped > 0.05:
            return NativeVerdict(False, "garbled", evidence)
    return NativeVerdict(True, "ok", evidence)
