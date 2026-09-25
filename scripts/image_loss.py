#!/usr/bin/env python3
"""Informative images the output drops (plan P4-4): the measure behind "有信息的图片被丢弃 = 0".

    uv run python scripts/image_loss.py [--config configs/regression_v2.yaml] [--doc NAME ...] [--allow-calls]

Runs the fixed pipeline v2 on every ground-truth document (offline replay by default) and looks at every image
the output does not show: excluded as decorative, or hidden otherwise.  Each is read with the local reader
(rapidocr, the page reading's engine); an image is *informative* when the reading has text, and *lost* when that
text is also in the document's annotation (``expected.md``) but not in the output.  Reported per image, with its
size and why it is hidden.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from parserx.config.schema import load_config  # noqa: E402
from parserx.ir.anchor import AssetAnchor  # noqa: E402
from parserx.ir.enums import BlockKind  # noqa: E402
from parserx.reading.local import LocalReader  # noqa: E402
from parserx.runtimes.pipeline import RuntimeFailure, run  # noqa: E402
from parserx.workspace import Workspace  # noqa: E402
from parserx.workspace.queries import HIDDEN  # noqa: E402

GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public")
MIN_CHARS = 2  # letters or digits a reading must have to count as text (one stray glyph is noise)


def _norm(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).lower() if c.isalnum())


def _inputs(names: set[str] | None):
    for gt in GT_DIRS:
        for d in sorted(gt.iterdir()):
            src = next((d / f"input{e}" for e in (".pdf", ".docx", ".doc") if (d / f"input{e}").is_file()), None)
            if src is not None and (d / "expected.md").is_file() and (names is None or d.name in names):
                yield d.name, src, (d / "expected.md").read_text(encoding="utf-8")


def _png(path: Path) -> bytes:
    import io

    from PIL import Image

    with Image.open(path) as image:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG")
        return buf.getvalue()


def measure(config, names: set[str] | None) -> list[dict]:
    reader = LocalReader()
    rows = []
    for name, src, expected in _inputs(names):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                outcome = run(src, Path(tmp) / "ws", Path(tmp) / "out", config)
            except RuntimeFailure as exc:
                rows.append({"doc": name, "error": str(exc)})
                continue
            ws = Workspace.open(Path(tmp) / "ws")
            state = ws.load()
            output = _norm(outcome.markdown)
            wanted = _norm(expected)
            assets = {a.id: a for a in state.assets}
            routes = {r.id: r for r in state.images}
            for block in state.blocks:
                if block.kind != BlockKind.FIGURE or block.status not in HIDDEN:
                    continue
                asset = next((assets[a.asset] for a in block.anchors if isinstance(a, AssetAnchor)), None)
                if asset is None:
                    continue
                lines = [text for _, text, score in reader.read(_png(ws.root / asset.path)) if score >= 0.5]
                text = " ".join(lines)
                chars = _norm(text)
                in_expected = [ln for ln in lines if len(_norm(ln)) >= MIN_CHARS and _norm(ln) in wanted]
                lost = [ln for ln in in_expected if _norm(ln) not in output]
                why = next((d.reason for d in reversed(block.decisions) if d.stage.value in ("image_route", "exclude")),
                           block.status.value)
                route = routes.get(asset.id)
                rows.append({"doc": name, "block": block.id, "size": f"{asset.width}x{asset.height}",
                             "route": route.route.value if route else None, "why": why[:60],
                             "text": text[:60], "informative": len(chars) >= MIN_CHARS,
                             "in_expected": len(in_expected), "lost": lost})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "configs" / "regression_v2.yaml")
    parser.add_argument("--doc", action="append", help="only these documents (repeatable)")
    parser.add_argument("--allow-calls", action="store_true", help="request what the cache lacks")
    args = parser.parse_args()
    config = load_config(args.config)
    config.cache.mode = "read_write" if args.allow_calls else "read_only"
    rows = measure(config, set(args.doc) if args.doc else None)
    print("| 文档 | 块 | 尺寸 | 路由 | 隐藏原因 | 本地读数 | 标注中有 | 输出中缺 |")
    print("|---|---|---|---|---|---|---|---|")
    hidden = informative = lost = 0
    for r in rows:
        if "error" in r:
            print(f"| {r['doc']} | 失败：{r['error'][:60]} | | | | | | |")
            continue
        hidden += 1
        informative += r["informative"]
        lost += bool(r["lost"])
        if r["informative"]:
            print(f"| {r['doc']} | {r['block']} | {r['size']} | {r['route']} | {r['why']} | {r['text']} "
                  f"| {r['in_expected']} | {'; '.join(r['lost'])[:60] or '—'} |")
    print(f"\nhidden images {hidden} · with text {informative} · text in the annotation and missing from the "
          f"output {lost}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
