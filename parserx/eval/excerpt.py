"""An excerpt of a Word document for test data: chosen body elements kept in order, the rest dropped, and the
images nothing refers to any more removed from the package, so the excerpt is as small as its content.

A part is named by body element positions (``w:body`` children counted from 0): ``"110-127"`` keeps a range,
``"311:9"`` keeps one element with only its first 9 images.  The document's final section properties are always
kept.  Images are only ever removed: text, tables, styles, numbering, headers and footers stay as they were.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOCUMENT = "word/document.xml"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
_PRUNABLE = ("media/", "embeddings/")


@dataclass(frozen=True)
class Part:
    first: int
    last: int
    images: int | None = None  # keep only the first so many images of the element (single elements only)


def parse_parts(specs: list[str]) -> list[Part]:
    """``["0-109", "311:9", "686"]`` → parts; overlapping or unordered specs are refused."""
    parts = []
    for spec in specs:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?(?::(\d+))?", spec.strip())
        if not m or (m.group(2) and m.group(3)):
            raise ValueError(f"not a part: {spec!r} (use 12, 12-40 or 12:3)")
        first = int(m.group(1))
        last = int(m.group(2)) if m.group(2) else first
        if last < first:
            raise ValueError(f"range runs backwards: {spec!r}")
        parts.append(Part(first, last, int(m.group(3)) if m.group(3) else None))
    for a, b in zip(parts, parts[1:]):
        if b.first <= a.last:
            raise ValueError(f"parts must be in order and apart: {a} then {b}")
    return parts


def _image_runs(element) -> list:
    """Runs of *element* that carry an image (DrawingML or VML), in document order."""
    runs = []
    for run in element.iter(f"{{{W}}}r"):
        if any(etree.QName(e).localname in ("drawing", "pict", "object") for e in run):
            runs.append(run)
    return runs


def _referenced_ids(root) -> set[str]:
    """Relationship ids the part refers to (any attribute in the relationships namespace)."""
    return {v for e in root.iter() for k, v in e.attrib.items() if k.startswith(f"{{{R}}}")}


def excerpt_docx(src: Path, dst: Path, parts: list[Part]) -> dict:
    """Write the excerpt of *src* to *dst*; returns counts of kept elements and images."""
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        data = {n: z.read(n) for n in names}
    doc = etree.fromstring(data[DOCUMENT])
    body = doc.find(f"{{{W}}}body")
    children = list(body)
    final_sect = children[-1] if etree.QName(children[-1]).localname == "sectPr" else None
    if parts and parts[-1].last >= len(children) - (final_sect is not None):
        raise ValueError(f"the body has {len(children)} elements; part {parts[-1]} runs past it")
    keep: dict[int, int | None] = {}
    for part in parts:
        for i in range(part.first, part.last + 1):
            keep[i] = part.images
    images = 0
    for i, element in enumerate(children):
        if element is final_sect:
            continue
        if i not in keep:
            body.remove(element)
            continue
        runs = _image_runs(element)
        limit = keep[i]
        if limit is not None:
            for run in runs[limit:]:
                run.getparent().remove(run)
            runs = runs[:limit]
        images += len(runs)
    data[DOCUMENT] = etree.tostring(doc, xml_declaration=True, encoding="UTF-8", standalone=True)

    # Relationships of the document body to images no longer referenced go, then the image files no package part
    # refers to any more.
    used = _referenced_ids(doc)
    rels = etree.fromstring(data[DOCUMENT_RELS])
    for rel in list(rels):
        target = rel.get("Target", "")
        if rel.get("TargetMode") != "External" and target.startswith(_PRUNABLE) and rel.get("Id") not in used:
            rels.remove(rel)
    data[DOCUMENT_RELS] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
    still = set()
    for name, xml in data.items():
        if name.endswith(".rels"):
            base = Path(name).parent.parent  # word/_rels/x.rels → word
            for rel in etree.fromstring(xml):
                if rel.get("TargetMode") != "External":
                    still.add(str((base / rel.get("Target", "")).as_posix()).replace("/./", "/"))
    removed = [n for n in names if n.startswith(tuple(f"word/{p}" for p in _PRUNABLE)) and n not in still]
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for name in names:
            if name not in removed:
                out.writestr(name, data[name])
    return {"elements": len(keep), "images": images, "files_removed": len(removed)}
