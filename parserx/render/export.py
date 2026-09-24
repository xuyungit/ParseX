"""Write an export: ``<name>.md``, ``<name>.blocks.json`` and the images the Markdown shows."""

from __future__ import annotations

import shutil
from pathlib import Path

from parserx.ir.anchor import AssetAnchor
from parserx.ir.enums import BlockKind, BlockStatus
from parserx.ir.state import DocumentState
from parserx.render.markdown import image_file, render_markdown
from parserx.render.sidecar import sidecar_json

IMAGE_DIR = "images"
_SHOWN = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})


def write_export(state: DocumentState, ws_root: Path | str, out_dir: Path | str, name: str) -> tuple[Path, Path]:
    """Returns (markdown path, sidecar path). Only images of rendered figure / scan blocks are copied."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    assets = {a.id: a for a in state.assets}
    for block in state.blocks:
        if block.status not in _SHOWN or block.kind not in (BlockKind.FIGURE, BlockKind.SCAN):
            continue
        for anchor in block.anchors:
            if isinstance(anchor, AssetAnchor) and anchor.asset in assets:
                asset = assets[anchor.asset]
                target = out / IMAGE_DIR / image_file(asset)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(Path(ws_root) / asset.path, target)
    md_path = out / f"{name}.md"
    md_path.write_text(render_markdown(state, image_dir=IMAGE_DIR), encoding="utf-8")
    sidecar_path = out / f"{name}.blocks.json"
    sidecar_path.write_text(sidecar_json(state), encoding="utf-8")
    return md_path, sidecar_path
