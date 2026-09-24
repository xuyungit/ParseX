"""Legacy .doc input: converted to .docx by LibreOffice (guide §2.1) into a scratch directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def doc_to_docx(path: Path, out_dir: Path) -> Path:
    """Convert *path* into *out_dir* (never next to the input) and return the .docx path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(path)],
        capture_output=True, text=True, timeout=120,
    )
    target = out_dir / f"{path.stem}.docx"
    if result.returncode != 0 or not target.exists():
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"LibreOffice could not convert {path.name}: {detail or result.returncode}")
    return target
