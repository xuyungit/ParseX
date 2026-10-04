"""The local recognizer behind the page reading (guide §9.5, Q56): rapidocr's PP-OCR ONNX models (shipped with
the package) on CPU, detection and recognition, no angle classifier.  Free and offline, about half a second
per page; its readings are local results kept in the derived cache, never requests."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
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
# an image instead of 4 (about 0.8 s on this Mac), and the same turn for every image.
ON_END = 0.5  # share of the lines standing on end (taller than wide) that says the image is turned a quarter
FEW = 30  # characters read as it stands below which the image is read turned a quarter too
UNSURE = 0.9  # the reading's mean confidence below which the image may be upside down
CLEAR = 1.5  # how much more level text a turn must read than the image as it stands, to be taken
ENOUGH = 25.0  # level text (characters × confidence) below which a reading tells no way up


@dataclass
class Upright:
    turn: int  # degrees clockwise that set the image upright: 0, 90, 180 or 270
    lines: list[Line]  # the reading of the image so turned (boxes in its pixels)
    level: dict[int, float]  # turn → level text read (``level_text``), for each turn read


def upright(reader, data: bytes, cache: ResponseCache | None) -> Upright:
    """Which way up an image reads (Q150).  Scanned certificates and statements are often put in turned a quarter
    or upside down, and read as they stand they are garbage.  The image is read as it stands; when its lines stand
    on end or almost nothing is read, turned a quarter each way too; when the reading is unsure, turned half way.
    The recognizer turns a line crop standing on end by itself, so a turn a quarter off still reads — its lines
    standing on end: the way up is the turn that reads the most text in level lines (``level_text``), taken only
    when it reads ``CLEAR`` times what the image as it stands does, and at least ``ENOUGH``."""
    readings = {0: read_cached(reader, data, cache)}
    lines = readings[0]
    chars = sum(len(t) for _, t, _ in lines)
    on_end = sum(1 for (x0, y0, x1, y1), t, _ in lines if len(t) > 1 and y1 - y0 > 1.5 * (x1 - x0))
    across = sum(1 for (x0, y0, x1, y1), t, _ in lines if len(t) > 1 and x1 - x0 > 1.5 * (y1 - y0))
    turns = ([90, 270] if chars < FEW or on_end > ON_END * max(on_end + across, 1) else []) \
        + ([180] if chars and _confident(lines) / chars < UNSURE else [])
    for turn in turns:
        readings[turn] = read_cached(reader, turn_image(data, turn), cache)
    level = {turn: level_text(found) for turn, found in readings.items()}
    best = max(level, key=level.get)
    if best and level[best] >= ENOUGH and level[best] >= CLEAR * level[0]:
        return Upright(best, readings[best], level)
    return Upright(0, readings[0], level)


def read_upright(reader, data: bytes, cache: ResponseCache | None) -> list[Line]:
    """The reading of an image the way up it reads best (``upright``)."""
    return upright(reader, data, cache).lines


def level_text(lines: list[Line]) -> float:
    """Letters and digits read in level lines (wider than tall), each by the line's confidence."""
    return sum(sum(ch.isalnum() for ch in t) * s for (x0, y0, x1, y1), t, s in lines if x1 - x0 >= y1 - y0)


def _confident(lines: list[Line]) -> float:
    return sum(len(t) * s for _, t, s in lines)


_TRANSPOSE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def turn_image(data: bytes, turn: int, media_type: str = "image/png") -> bytes:
    """*data* turned *turn* degrees clockwise, encoded as *media_type* (PNG, or JPEG at quality 95)."""
    out = io.BytesIO()
    with Image.open(io.BytesIO(data)) as image:
        turned = image.transpose(_TRANSPOSE[turn])
        if media_type == "image/jpeg":
            turned.convert("RGB").save(out, "JPEG", quality=95)
        else:
            turned.save(out, "PNG")
    return out.getvalue()
