"""Local layout detector (guide §6.2): pp_doc_layoutv3 through rapid-layout on the CPU.

The model is loaded once per process and only when an image is not in the
derived cache, so offline replays never load it.  rapid-layout downloads the
model on first use; unit tests use a fake detector (``live_layout`` tests use
the real one).  Output order and precision are fixed (boxes rounded to 0.1 px,
scores to 0.001) so the same image yields the same regions.

onnxruntime's telemetry is switched off: it sends usage events off the machine,
and where it cannot write its device id (a sandbox) it leaves ``:memory:.ses``
in the working directory (P2-4 F4).
"""

from __future__ import annotations

from pathlib import Path

import hashlib
import io
import logging
import os
import threading
from dataclasses import dataclass
from importlib import metadata
from typing import Protocol

import numpy as np
from PIL import Image

from parserx.cache import ResponseCache
from parserx.ir.base import BBox

os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")  # read when onnxruntime starts its first session


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

    def __init__(self, model: str = "pp_doc_layoutv3", conf_thresh: float = 0.5, layout=None):
        self.model = model
        self.conf_thresh = conf_thresh
        self.layout = layout  # LayoutConfig: where the model file lives (fetched on first use); None: rapid-layout's
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

                    where = {"model_dir_or_path": str(ensure_model(self.layout))} if self.layout is not None else {}
                    self._engines[key] = RapidLayout(model_type=ModelType(self.model), conf_thresh=self.conf_thresh,
                                                     **where)
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


# ── the model file (release R5, Q111) ───────────────────────────────────


def model_file(layout) -> Path:
    """Where the layout model's ONNX file is expected (``LayoutConfig``): a path placed by hand, or the model cache."""
    if layout.model_path:
        return Path(layout.model_path).expanduser()
    return Path(layout.model_dir).expanduser() / f"{layout.model}.onnx"


def model_source(model: str) -> tuple[str, str | None]:
    """The URL and SHA-256 rapid-layout publishes for *model*."""
    import yaml
    from importlib.resources import files

    table = yaml.safe_load((files("rapid_layout") / "configs" / "default_models.yaml").read_text(encoding="utf-8"))
    entry = table[model]
    return entry["model_dir_or_path"], entry.get("SHA256")


def ensure_model(layout, progress=None) -> Path:
    """The model file, downloaded first when it is not there (to a temporary name, checked, then renamed).
    *progress* is called with (bytes so far, total bytes or None)."""
    import hashlib
    import requests

    path = model_file(layout)
    if path.is_file():
        return path
    if layout.model_path:
        raise FileNotFoundError(f"layout.model_path {path} does not exist")
    url, sha256 = model_source(layout.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    digest = hashlib.sha256()
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0) or None
        done = 0
        with open(partial, "wb") as out:
            for chunk in response.iter_content(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    if sha256 and digest.hexdigest() != sha256.lower():
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"the downloaded layout model does not match its SHA-256 ({url})")
    partial.replace(path)
    return path
