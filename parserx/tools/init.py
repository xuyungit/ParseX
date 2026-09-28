"""``workspace init``: extract the input and create the workspace (not one of the seven tools)."""

from __future__ import annotations

import hashlib
import tempfile
import time
from collections import Counter
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.content.convert import doc_to_docx
from parserx.content.docx import extract_docx
from parserx.content.pdf_native import extract_pdf
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockKind
from parserx.tools.envelope import Cost, Envelope, Failure, FailureCode
from parserx.tools.views import DocInfo, doc_info
from parserx.workspace import Workspace
from parserx.workspace.views import PageRow, page_rows


class InitResult(IRModel):
    doc: DocInfo
    pages: list[PageRow]
    blocks_by_kind: dict[BlockKind, int]
    ledger_items: int
    assets: int
    warnings: list[str]


def workspace_init(input_path: Path | str, ws_dir: Path | str, *, config: ParserXConfig) -> tuple[Envelope, int]:
    started = time.monotonic()
    source, ws_dir = Path(input_path), Path(ws_dir)

    def fail(code: FailureCode, message: str, exit_code: int) -> tuple[Envelope, int]:
        return Envelope(tool="workspace_init", doc="", ws_version=0, ok=False,
                        failures=[Failure(code=code, message=message, retryable=False)]), exit_code

    if not source.is_file():
        return fail(FailureCode.NOT_FOUND, f"no input file {source}", 2)
    if ws_dir.exists() and any(ws_dir.iterdir()):
        return fail(FailureCode.INVALID_REQUEST, f"{ws_dir} is not empty", 2)
    suffix = source.suffix.lower()
    if suffix not in (".pdf", ".docx", ".doc"):
        return fail(FailureCode.INVALID_REQUEST, f"unsupported input type {suffix!r}", 2)
    with tempfile.TemporaryDirectory(prefix="parserx-init-") as scratch:
        try:
            readable = doc_to_docx(source, Path(scratch)) if suffix == ".doc" else source
            ext = (extract_pdf(readable, layout=_page_layout(config), check_tables=config.layout.check_tables)
                   if suffix == ".pdf"
                   else extract_docx(readable))
        except Exception as exc:  # noqa: BLE001 - an unreadable input is reported, not raised
            return fail(FailureCode.INVALID_REQUEST, f"input could not be read: {type(exc).__name__}: {exc}", 2)
        state = ext.to_state(doc_id=source.stem, source=source.name,
                             source_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        if suffix == ".doc":
            state.warnings.insert(0, "converted from .doc to .docx with LibreOffice before reading")
        ws = Workspace.create(ws_dir, state, readable, files=ext.asset_bytes)
    state = ws.load()
    result = InitResult(
        doc=doc_info(state), pages=page_rows(state),
        blocks_by_kind=dict(sorted(Counter(b.kind for b in state.blocks).items())),
        ledger_items=len(state.ledger), assets=len(state.assets), warnings=state.warnings,
    )
    envelope = Envelope(tool="workspace_init", doc=state.id, ws_version=state.version, ok=True, result=result,
                        cost=Cost(wall_s=round(time.monotonic() - started, 3)))
    ws.log_call({"tool": "workspace_init", "request": {"input": str(source)},
                 "envelope": envelope.model_dump(mode="json", exclude={"result"})})
    return envelope, 0


def _page_layout(config: ParserXConfig):
    """The regions the local layout detector sees on a page, (label, bbox in page points of the unrotated page):
    its text regions make the paragraphs (Q80), its table regions confirm ruled grids (Phase 3 D3).  The render is
    the layout step's, so the detections are cached and not repeated there."""
    import pymupdf

    from parserx.cache.store import open_cache
    from parserx.layout.detector import RapidLayoutDetector, detect_cached

    detector = RapidLayoutDetector(config.layout.model, config.layout.conf_thresh, layout=config.layout)
    cache = open_cache(config.cache)
    dpi = config.layout.page_dpi

    def regions(page) -> list[tuple[str, tuple[float, float, float, float]]]:
        png = page.get_pixmap(dpi=dpi).tobytes("png")
        k, back = 72.0 / dpi, page.derotation_matrix
        return [(r.label, tuple(pymupdf.Rect(*(v * k for v in r.bbox)) * back)) for r in detect_cached(detector, png, cache)]

    return regions
