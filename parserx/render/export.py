"""Write the output package (guide §4.5, Q42): ``<name>.md``, ``images/``, ``<name>.json``, ``<name>.blocks.json``.

``images/`` holds every extracted image — shown or not (a decorative icon is kept, not linked); the Markdown
links only the images it shows.  Every runtime writes the same package.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from parserx.ir.state import DocumentState
from parserx.render.markdown import image_file, render_markdown
from parserx.render.sidecar import sidecar_json
from parserx.render.summary import package_images, summary_json

IMAGE_DIR = "images"


@dataclass(frozen=True)
class ExportPaths:
    markdown: Path
    sidecar: Path
    summary: Path
    images: Path


def write_export(state: DocumentState, ws_root: Path | str, out_dir: Path | str, name: str) -> ExportPaths:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images = out / IMAGE_DIR
    for asset in package_images(state):
        target = images / image_file(asset)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(ws_root) / asset.path, target)
    md_path = out / f"{name}.md"
    md_path.write_text(render_markdown(state, image_dir=IMAGE_DIR), encoding="utf-8")
    sidecar_path = out / f"{name}.blocks.json"
    sidecar_path.write_text(sidecar_json(state), encoding="utf-8")
    summary_path = out / f"{name}.json"
    summary_path.write_text(summary_json(state, name, IMAGE_DIR), encoding="utf-8")
    return ExportPaths(markdown=md_path, sidecar=sidecar_path, summary=summary_path, images=images)
