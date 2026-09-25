"""The local recognizer behind the page reading (guide §9.5, Q56): rapidocr's PP-OCR ONNX models (shipped with
the package) on CPU, detection and recognition, no angle classifier.  Free and offline, about half a second
per page; its readings are local results kept in the derived cache, never requests."""

from __future__ import annotations

import hashlib
from importlib.metadata import version as _version
from typing import Any

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
