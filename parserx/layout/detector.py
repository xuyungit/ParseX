"""Local layout detector (guide §6.2): pp_doc_layoutv3 through rapid-layout on the CPU.

The model is loaded once per process and only when an image is not in the
derived cache, so offline replays never load it.  rapid-layout downloads the
model on first use; unit tests use a fake detector (``live_layout`` tests use
the real one).  Output order and precision are fixed (boxes rounded to 0.1 px,
scores to 0.001) so the same image yields the same regions.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol

import numpy as np
from PIL import Image

from parserx.cache import ResponseCache
from parserx.ir.base import BBox


@dataclass(frozen=True)
class Region:
    bbox: BBox  # image pixels
    label: str  # detector label, mapped only in layout/labels.py
    score: float


class Detector(Protocol):
    name: str
    version: str

    def detect(self, png: bytes) -> list[Region]: ...


def decode(png: bytes) -> np.ndarray:
    """RGB uint8 array of an encoded image."""
    with Image.open(io.BytesIO(png)) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


class RapidLayoutDetector:
    _engines: dict[tuple[str, float], object] = {}
    _lock = threading.Lock()

    def __init__(self, model: str = "pp_doc_layoutv3", conf_thresh: float = 0.5):
        self.model = model
        self.conf_thresh = conf_thresh
        self.name = "layout"
        self.version = f"{model}@rapid-layout-{metadata.version('rapid-layout')}"

    def _engine(self):
        key = (self.model, self.conf_thresh)
        with self._lock:
            if key not in self._engines:
                # rapid-layout pins its own loggers to INFO on creation; keep its start-up chatter out of our logs.
                previous = logging.root.manager.disable
                logging.disable(logging.INFO)
                try:
                    from rapid_layout import ModelType, RapidLayout

                    self._engines[key] = RapidLayout(model_type=ModelType(self.model), conf_thresh=self.conf_thresh)
                finally:
                    logging.disable(previous)
                for name in list(logging.root.manager.loggerDict):
                    if name.startswith(("rapid_layout", "RapidLayout")):
                        logging.getLogger(name).setLevel(logging.WARNING)
            return self._engines[key]

    def detect(self, png: bytes) -> list[Region]:
        out = self._engine()(decode(png))
        regions = [Region(bbox=tuple(round(float(v), 1) for v in box), label=str(label), score=round(float(score), 3))
                   for box, label, score in zip(out.boxes or [], out.class_names or [], out.scores or [])]
        return sorted(regions, key=lambda r: (r.bbox[1], r.bbox[0], r.label))


def detect_cached(detector: Detector, png: bytes, cache: ResponseCache | None) -> list[Region]:
    """Detections of *png*, from the derived cache when present (they are local, not requests)."""
    key = hashlib.sha256(detector.version.encode() + b"\0" + png).hexdigest()
    if cache is not None:
        hit, value = cache.get_derived("layout", key)
        if hit:
            return [Region(bbox=tuple(r["bbox"]), label=r["label"], score=r["score"]) for r in value]
    regions = detector.detect(png)
    if cache is not None:
        cache.put_derived("layout", key, [{"bbox": list(r.bbox), "label": r.label, "score": r.score} for r in regions])
    return regions
