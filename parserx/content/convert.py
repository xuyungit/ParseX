"""Legacy .doc input: converted to .docx by LibreOffice (guide §2.1) into a scratch directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def doc_to_docx(path: Path, out_dir: Path) -> Path:
    """Convert *path* into *out_dir* (never next to the input) and return the .docx path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(path)],
        capture_output=True, text=True, timeout=120,
    )
    target = out_dir / f"{path.stem}.docx"
    if result.returncode != 0 or not target.exists():
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"LibreOffice could not convert {path.name}: {detail or result.returncode}")
    return target


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")
_FALLBACK_DPI = 150  # a scan without a resolution is taken as a 150 dpi page


def image_to_pdf(path: Path, out_dir: Path) -> Path:
    """An image file as a PDF of scanned pages (Q119): one page per image, one per frame of a multi-page TIFF, sized
    from the image's resolution (150 dpi when it has none), so the page is read like any scanned page."""
    import io

    import pymupdf
    from PIL import Image, ImageSequence

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pymupdf.open()
    with Image.open(path) as image:
        for frame in ImageSequence.Iterator(image):
            dpi = frame.info.get("dpi") or image.info.get("dpi") or (_FALLBACK_DPI, _FALLBACK_DPI)
            x_dpi, y_dpi = (float(d) if d and float(d) >= 72 else _FALLBACK_DPI for d in dpi[:2])
            png = io.BytesIO()
            frame.convert("RGB" if frame.mode not in ("L", "RGB") else frame.mode).save(png, format="PNG")
            width, height = frame.size[0] * 72 / x_dpi, frame.size[1] * 72 / y_dpi
            page = pdf.new_page(width=width, height=height)
            page.insert_image(page.rect, stream=png.getvalue())
    target = out_dir / f"{path.stem}.pdf"
    pdf.save(target)
    return target
