"""The local recognizer behind the page reading (guide §9.5, Q56): rapidocr's PP-OCR ONNX models (shipped with
the package) on CPU, detection and recognition, no angle classifier.  Free and offline, about half a second
per page; its readings are local results kept in the derived cache, never requests."""

from __future__ import annotations

import hashlib
import io
from importlib.metadata import version as _version
from typing import Any

from PIL import Image

from parserx.cache.store import ResponseCache
from parserx.layout.detector import decode

Line = tuple[tuple[float, float, float, float], str, float]  # (bbox in image pixels, text, confidence)


class LocalReader:
    name = "reading"

    def __init__(self) -> None:
        self.version = f"rapidocr-{_version('rapidocr')}"
        self._engine: Any = None

    def read(self, png: bytes) -> list[Line]:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR(params={"Global.log_level": "error"})
        result = self._engine(decode(png)[:, :, :3], use_cls=False)
        if result.boxes is None:
            return []
        lines = []
        for box, text, score in zip(result.boxes.tolist(), result.txts, result.scores):
            xs, ys = [p[0] for p in box], [p[1] for p in box]
            lines.append(((min(xs), min(ys), max(xs), max(ys)), str(text), round(float(score), 3)))
        return lines


def read_cached(reader, png: bytes, cache: ResponseCache | None) -> list[Line]:
    """The reading of *png*, from the derived cache when present."""
    key = hashlib.sha256(reader.version.encode() + b"\0" + png).hexdigest()
    if cache is not None:
        hit, value = cache.get_derived("reading", key)
        if hit:
            return [(tuple(v[0]), v[1], v[2]) for v in value]
    lines = reader.read(png)
    if cache is not None:
        cache.put_derived("reading", key, [[list(b), t, s] for b, t, s in lines])
    return lines


# Measured on the 385 images of one bid document (2026-10-04), against reading every image all four ways: 2.1 readings
# an image instead of 4 (about 0.8 s on this Mac), and the same best reading for every image.
ON_END = 0.5  # share of the lines standing on end (taller than wide) that says the image is turned a quarter
FEW = 30  # characters read upright below which the image is read turned a quarter too
UNSURE = 0.9  # the reading's mean confidence below which the image may be upside down


def read_upright(reader, data: bytes, cache: ResponseCache | None) -> list[Line]:
    """The reading of an image the way up it reads best (Q150).  Scanned certificates and statements are often put in
    turned a quarter or upside down (a third of the images of one bid document), and the reader has no angle
    classifier: turned text comes back as garbage.  The image is read as it stands; when its lines stand on end, or
    almost nothing is read, it is read turned a quarter each way too, and when the reading is unsure, turned half
    way; the reading with the most confident characters wins.  Boxes are in the pixels of the image as turned: the
    reading is for its text, not for places."""
    lines = read_cached(reader, data, cache)
    chars = sum(len(t) for _, t, _ in lines)
    on_end = sum(1 for (x0, y0, x1, y1), t, _ in lines if len(t) > 1 and y1 - y0 > 1.5 * (x1 - x0))
    across = sum(1 for (x0, y0, x1, y1), t, _ in lines if len(t) > 1 and x1 - x0 > 1.5 * (y1 - y0))
    turns = ([90, 270] if chars < FEW or on_end > ON_END * max(on_end + across, 1) else []) \
        + ([180] if chars and _confident(lines) / chars < UNSURE else [])
    for angle in turns:
        other = read_cached(reader, _turned(data, angle), cache)
        if _confident(other) > _confident(lines):
            lines = other
    return lines


def _confident(lines: list[Line]) -> float:
    return sum(len(t) * s for _, t, s in lines)


def _turned(data: bytes, angle: int) -> bytes:
    out = io.BytesIO()
    with Image.open(io.BytesIO(data)) as image:
        image.convert("RGB").rotate(angle, expand=True).save(out, "PNG")
    return out.getvalue()

