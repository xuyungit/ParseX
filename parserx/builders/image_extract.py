"""Image extractor — save images from documents to disk.

Supports PDF (via PyMuPDF xref) and DOCX (via Docling PictureItem).
Saves images to an output directory and updates element metadata
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

                # Vector figures carry pre-rendered PNG data.
                if elem.metadata.get("vector_figure"):
                    saved_path = self._save_vector_figure(
                        elem, page.number, images_dir
                    )
                    if saved_path:
                        elem.metadata["saved_path"] = f"images/{saved_path.name}"
                        elem.metadata["saved_abs_path"] = str(saved_path)
                        extracted_count += 1
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
        if images_dir.exists() and not any(images_dir.iterdir()):
            images_dir.rmdir()
        log.info("Extracted %d images, skipped %d", extracted_count, skipped_count)
        return doc

    def extract_docx(self, doc: Document, source_path: Path, output_dir: Path) -> Document:
        """Extract images from DOCX via Docling and save them.

        Uses Docling's PictureItem.get_image() to obtain PIL images.
        """
        from docling.document_converter import DocumentConverter

        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        converter = DocumentConverter()
        result = converter.convert(str(source_path))
        docling_doc = result.document

        extracted_count = 0
        skipped_count = 0
        img_counter = 0

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue
                if elem.metadata.get("skipped"):
                    skipped_count += 1
                    continue

                self_ref = elem.metadata.get("docling_self_ref")
                if not self_ref:
                    continue

                # Look up the PictureItem in the Docling document
                try:
                    picture_item = docling_doc.get_ref(self_ref)
                    pil_image = picture_item.get_image(docling_doc)
                except Exception:
                    pil_image = None

                if pil_image is None:
                    continue

                img_counter += 1
                filename = f"p{page.number}_img{img_counter}.png"
                output_path = images_dir / filename
                pil_image.save(output_path, format="PNG")

                elem.metadata["saved_path"] = f"images/{filename}"
                elem.metadata["saved_abs_path"] = str(output_path)

                # Update dimensions from actual image if not already set
                if not elem.metadata.get("width"):
                    elem.metadata["width"] = pil_image.width
                    elem.metadata["height"] = pil_image.height

                extracted_count += 1

        if images_dir.exists() and not any(images_dir.iterdir()):
            images_dir.rmdir()
        log.info("Extracted %d DOCX images, skipped %d", extracted_count, skipped_count)
        return doc

    _vector_counter = 0  # class-level counter for unique filenames

    def _save_vector_figure(
        self, elem, page_number: int, output_dir: Path
    ) -> Path | None:
        """Save a pre-rendered vector figure PNG to disk."""
        png_data = elem.metadata.get("pixmap_png")
        if not png_data:
            return None

        ImageExtractor._vector_counter += 1
        filename = f"p{page_number}_vec{ImageExtractor._vector_counter}.png"
        output_path = output_dir / filename
        output_path.write_bytes(png_data)

        # Free the PNG bytes from metadata (no longer needed after saving).
        del elem.metadata["pixmap_png"]

        return output_path

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
