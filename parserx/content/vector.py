"""Vector images (EMF / WMF) rendered to PNG (P2-4 C4): Markdown viewers and vision models cannot read them.

LibreOffice converts every drawing of a document to a one-page PDF in one process (a private profile, so a
LibreOffice the user has open does not interfere); PyMuPDF then renders what is drawn — the page LibreOffice puts
it on is left out — at a fixed resolution.  The result is local and byte-stable for the same versions.  A drawing
LibreOffice cannot read (it then opens the bytes as a text document, not as a drawing), or a machine without
LibreOffice, gives no rendering: the caller keeps the original.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import fitz

VECTOR_MEDIA = {"image/x-emf": ".emf", "image/emf": ".emf", "image/x-wmf": ".wmf", "image/wmf": ".wmf"}
RENDERER = "libreoffice"
DPI = 200
MAX_SIDE = 2400  # px, longest side
_DRAWN = ("fill-", "stroke-")  # bbox-log kinds that paint (clips and ignored text do not)
_BACKGROUND = 0.98  # a fill covering this share of the page is the page, not the drawing


@dataclass
class Rendered:
    images: dict[str, bytes] = field(default_factory=dict)  # name → PNG, for the drawings that could be rendered
    version: str | None = None  # the LibreOffice that rendered them


def render_vectors(items: dict[str, tuple[bytes, str]]) -> Rendered:
    """Render each item (name → (bytes, media type)); the ones that cannot be rendered are left out."""
    out = Rendered()
    if not items or shutil.which("soffice") is None:
        return out
    with tempfile.TemporaryDirectory(prefix="parserx-vector-") as tmp:
        root = Path(tmp)
        names = sorted(items)
        sources = []
        for index, name in enumerate(names):
            data, media = items[name]
            source = root / f"{index:04d}{VECTOR_MEDIA.get(media, '.emf')}"
            source.write_bytes(data)
            sources.append(source)
        try:
            subprocess.run(["soffice", f"-env:UserInstallation={(root / 'profile').as_uri()}", "--headless",
                            "--convert-to", "pdf", "--outdir", str(root / "pdf"), *map(str, sources)],
                           capture_output=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            return out
        for name, source in zip(names, sources):
            pdf = root / "pdf" / f"{source.stem}.pdf"
            version = _drawing(pdf.read_bytes()) if pdf.exists() else None
            png = render_drawing(pdf.read_bytes()) if version is not None else None
            if png is not None:
                out.images[name] = png
                out.version = out.version or version
        return out


def _drawing(pdf: bytes) -> str | None:
    """The LibreOffice version when *pdf* was made from a drawing (Draw), None when from anything else."""
    try:
        with fitz.open(stream=pdf, filetype="pdf") as doc:
            meta = doc.metadata or {}
    except Exception:  # noqa: BLE001 - unreadable output: not a drawing
        return None
    if meta.get("creator") != "Draw":
        return None
    producer = (meta.get("producer") or "").split()
    return producer[1] if len(producer) > 1 and producer[0] == "LibreOffice" else "unknown"


def render_drawing(pdf: bytes) -> bytes | None:
    """What is drawn on the first page of *pdf*, cropped to its extent, as PNG; None when nothing is drawn."""
    try:
        doc = fitz.open(stream=pdf, filetype="pdf")
    except Exception:  # noqa: BLE001 - LibreOffice wrote something PyMuPDF cannot open: no rendering
        return None
    with doc:
        if doc.page_count == 0:
            return None
        page = doc[0]
        area = page.rect.get_area()
        drawn = fitz.Rect()
        for kind, box in page.get_bboxlog():
            rect = fitz.Rect(box) & page.rect
            if not kind.startswith(_DRAWN) or rect.is_empty:
                continue
            if kind == "fill-path" and rect.get_area() >= _BACKGROUND * area:
                continue
            drawn |= rect
        if drawn.is_empty:
            return None
        zoom = min(DPI / 72, MAX_SIDE / max(drawn.width, drawn.height))
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=drawn, alpha=False).tobytes("png")
