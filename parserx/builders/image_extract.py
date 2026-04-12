"""Image extractor — save images from documents to disk.

Supports PDF (via PyMuPDF xref) and DOCX (via Docling PictureItem).
Saves images to an output directory and updates element metadata
with the saved file path.

Only extracts images that are not marked as skipped (decorative/blank).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageOps

from parserx.models.elements import Document
from parserx.processors.image import _bbox_overlap_ratio

log = logging.getLogger(__name__)


class ImageExtractor:
    """Extract images from PDF and save to output directory."""

    def __init__(self, vector_figure_render_dpi: int = 200):
        self._vfig_dpi = vector_figure_render_dpi

    def extract(self, doc: Document, source_path: Path, output_dir: Path) -> Document:
        """Extract non-skipped images and save them.

        Updates element.metadata["saved_path"] with relative path.
        """
        images_dir = output_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        fitz_doc = fitz.open(str(source_path))
        extracted_count = 0
        skipped_count = 0
        vfig_counter = 0

        # Prefer native images over OCR-detected vector figures
        deduped = self._dedup_vfig_native(doc)
        if deduped:
            log.info("Deduped %d vector figures (native image preferred)", deduped)

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue
                if elem.metadata.get("skipped"):
                    skipped_count += 1
                    continue

                # ── Vector figure: render page region ───────────────
                if elem.metadata.get("vector_figure"):
                    vfig_counter += 1
                    saved = self._render_vector_figure(
                        fitz_doc, elem, page.number, images_dir, vfig_counter,
                    )
                    if saved:
                        extracted_count += 1
                    continue

                # ── Normal image: extract by xref ───────────────────
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

    @staticmethod
    def _dedup_vfig_native(doc: Document, overlap_threshold: float = 0.5) -> int:
        """Suppress vector figures that overlap with native PDF images.

        When OCR layout detection creates a vector_figure element for the
        same region that already has a native embedded image (with xref),
        the native image is preferred because it preserves the original
        resolution and encoding.
        """
        suppressed = 0
        for page in doc.pages:
            vfigs = []
            native_imgs = []
            for elem in page.elements:
                if elem.type != "image" or elem.metadata.get("skipped"):
                    continue
                if elem.metadata.get("vector_figure"):
                    vfigs.append(elem)
                elif elem.metadata.get("xref"):
                    native_imgs.append(elem)

            for vfig in vfigs:
                for native in native_imgs:
                    if _bbox_overlap_ratio(vfig.bbox, native.bbox) > overlap_threshold:
                        vfig.metadata["skipped"] = True
                        vfig.metadata["skip_reason"] = "dedup_native_image_preferred"
                        suppressed += 1
                        break
        return suppressed

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

        log.info("Extracted %d DOCX images, skipped %d", extracted_count, skipped_count)
        return doc

    def _render_vector_figure(
        self,
        fitz_doc: fitz.Document,
        elem: "PageElement",  # noqa: F821
        page_number: int,
        output_dir: Path,
        counter: int,
    ) -> bool:
        """Render a vector figure region to PNG.

        Uses the element's bbox (in PDF points) to clip the page
        rendering.  Returns True if the image was saved successfully.
        """
        idx = page_number - 1
        if idx < 0 or idx >= len(fitz_doc):
            return False

        fitz_page = fitz_doc[idx]
        rect = fitz.Rect(elem.bbox)

        # Add 5% padding, clamped to page bounds
        pad = max(rect.width, rect.height) * 0.05
        clip = fitz.Rect(
            max(fitz_page.rect.x0, rect.x0 - pad),
            max(fitz_page.rect.y0, rect.y0 - pad),
            min(fitz_page.rect.x1, rect.x1 + pad),
            min(fitz_page.rect.y1, rect.y1 + pad),
        )

        scale = self._vfig_dpi / 72
        mat = fitz.Matrix(scale, scale)
        try:
            pix = fitz_page.get_pixmap(matrix=mat, clip=clip)
        except Exception as exc:
            log.warning("Failed to render vector figure on page %d: %s", page_number, exc)
            return False

        filename = f"p{page_number}_vfig{counter}.png"
        output_path = output_dir / filename
        pix.save(str(output_path))

        elem.metadata["saved_path"] = f"images/{filename}"
        elem.metadata["saved_abs_path"] = str(output_path)
        # Update pixel dimensions for image classification
        elem.metadata["width"] = pix.width
        elem.metadata["height"] = pix.height

        log.debug("Saved vector figure: %s (%dx%d)", filename, pix.width, pix.height)
        return True

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

        raw_bytes = img_data["image"]
        ext = img_data.get("ext", "png")

        # ImageMask images have inverted colors (black=foreground,
        # white=transparent).  PDF readers apply the mask correctly, but
        # raw extraction gives inverted output — fix it here.
        try:
            obj_str = fitz_doc.xref_object(xref)
            if "/ImageMask true" in obj_str or "/ImageMask True" in obj_str:
                im = Image.open(io.BytesIO(raw_bytes)).convert("L")
                im = ImageOps.invert(im)
                im = im.point(lambda x: 0 if x < 128 else 255, "1")
                buf = io.BytesIO()
                im.save(buf, "PNG")
                raw_bytes = buf.getvalue()
                ext = "png"
                log.debug("Inverted ImageMask for xref %d on page %d", xref, page_number)
        except Exception as exc:
            log.warning("ImageMask check failed for xref %d: %s", xref, exc)

        filename = f"p{page_number}_img{xref}.{ext}"
        output_path = output_dir / filename

        output_path.write_bytes(raw_bytes)
        return output_path
