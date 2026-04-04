"""Image extractor — save images from PDF to disk.

Extracts embedded images from the PDF using PyMuPDF xref,
saves them to an output directory, and updates element metadata
with the saved file path.

Only extracts images that are not marked as skipped (decorative/blank).
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from parserx.models.elements import Document

log = logging.getLogger(__name__)


class ImageExtractor:
    """Extract images from PDF and save to output directory."""

    def extract(self, doc: Document, source_path: Path, output_dir: Path) -> Document:
        """Extract non-skipped images and save them.

        Updates element.metadata["saved_path"] with relative path.
        """
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        fitz_doc = fitz.open(str(source_path))
        extracted_count = 0
        skipped_count = 0

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue
                if elem.metadata.get("skipped"):
                    skipped_count += 1
                    continue

                xref = elem.metadata.get("xref", 0)
                if xref == 0:
                    continue

                saved_path = self._extract_image(fitz_doc, xref, page.number, images_dir)
                if saved_path:
                    elem.metadata["saved_path"] = f"images/{saved_path.name}"
                    elem.metadata["saved_abs_path"] = str(saved_path)
                    extracted_count += 1

        fitz_doc.close()
        log.info("Extracted %d images, skipped %d", extracted_count, skipped_count)
        return doc

    def _extract_image(
        self, fitz_doc: fitz.Document, xref: int, page_number: int, output_dir: Path
    ) -> Path | None:
        """Extract a single image by xref and save it."""
        try:
            img_data = fitz_doc.extract_image(xref)
        except Exception:
            return None

        if not img_data or not img_data.get("image"):
            return None

        ext = img_data.get("ext", "png")
        filename = f"p{page_number}_img{xref}.{ext}"
        output_path = output_dir / filename

        output_path.write_bytes(img_data["image"])
        return output_path
