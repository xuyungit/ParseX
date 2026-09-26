"""Workspace state and the sidecar (docs/v2_phase1_interfaces.md §2.10, guide §4.5).

The sidecar is ``DocumentState`` as exported, plus an accounting summary
derived from the ledger at export time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from parserx.ir.anchor import SourceAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import BBox, IRModel
from parserx.ir.block import Block
from parserx.ir.enums import DocumentStatus, ImageRoute, PageStatus
from parserx.ir.relation import Relation

Disposition = Literal["output", "merged", "duplicate", "excluded", "failed"]

# What a ledger item is; one item per discovered unit (guide §2.3 principle 1).
LedgerUnit = Literal[
    "native_line",       # a PyMuPDF text line
    "pdf_image",         # an image placed on a PDF page
    "ocr_block",         # one parsing_res_list entry from the scan engine
    "docx_paragraph",
    "docx_table",
    "docx_image",
    "docx_deleted",      # text removed by a tracked deletion (excluded, guide §6.9)
    "docx_unsupported",  # element the Phase 1 reader does not handle (failed)
    "docx_note",         # a footnote or endnote (output as a Markdown footnote, Q9)
    "docx_comment",      # a reviewer's comment (excluded, Q9)
    "agent_text",        # text the agent read from a page image where no block had it, backed by the local reading (Q56)
]


class PageState(IRModel):
    n: int = Field(ge=1)
    unit: Literal["pdf_page", "docx_segment"]  # DOCX: segments between explicit page / section breaks
    status: PageStatus
    size_pt: tuple[float, float] | None = None
    render: str | None = None  # Asset id
    starts_with: Literal["page_break", "section_break"] | None = None  # DOCX: what opened this segment


class LedgerEntry(IRModel):
    """The smallest unit of content accounting; disposition None means unassigned."""

    item: str
    unit: LedgerUnit
    source: SourceAnchor
    chars: int = Field(ge=0)
    disposition: Disposition | None = None
    block: str | None = None  # the block that carries the content or the Decision explaining its fate


class ImageRecord(IRModel):
    id: str  # Asset id
    route: ImageRoute
    shown: bool
    t: float | None = None
    f: float | None = None
    regions: int = 0
    complete: bool | None = None


class Missing(IRModel):
    block: str
    reason: str


class TokenUsage(IRModel):
    input: int = 0
    cached_input: int = 0
    output: int = 0


class Stats(IRModel):
    """Run statistics. The only part of the sidecar that may differ between a fresh run and a replay."""

    requests: dict[str, int] = {}  # real network requests per service
    attempts: dict[str, int] = {}
    cache_hits: dict[str, int] = {}
    tokens: dict[str, TokenUsage] = {}
    cost_usd: float | None = None
    wall_time_s: float = 0.0


class ReadLine(IRModel):
    """One text line of a local page reading, in page points (unrotated PDF space, like every PdfAnchor)."""

    bbox: BBox
    text: str
    score: float  # the local recognizer's own confidence


class PageReading(IRModel):
    """An independent local reading of one page render (guide §9.5, Q56): evidence for the two-way comparison
    with the output, never output itself."""

    n: int = Field(ge=1)
    engine: str  # recognizer and version
    dpi: int
    lines: list[ReadLine] = []
    not_prose: list[BBox] = []  # layout-detector regions whose text is picture or formula content


class ClosedItem(IRModel):
    """A worklist signal the agent checked against the page image and left as it is (``close``).  It stays closed
    while the item reads the same (target, kind, quoted text); new content opens it again."""

    target: str
    kind: str
    quotes: list[str] = []
    reason: str
    actor: str
    image: str  # the image it was checked on
    occluded: bool = False  # text the page draws under another element: content, kept, and named in the summary (Q71)


class DocumentState(IRModel):
    schema_version: Literal[1] = 1
    id: str
    source: str
    source_sha256: str
    format: Literal["pdf", "docx"]
    status: DocumentStatus
    engines: dict[str, str] = {}
    prompt_hashes: dict[str, str] = {}
    pages: list[PageState] = []
    blocks: list[Block] = []
    relations: list[Relation] = []
    assets: list[Asset] = []
    images: list[ImageRecord] = []
    readings: list[PageReading] = []  # local page readings (guide §9.5, Q56)
    closed: list[ClosedItem] = []  # worklist signals checked and left as they are
    ledger: list[LedgerEntry] = []
    missing: list[Missing] = []
    stats: Stats = Stats()
    warnings: list[str] = []
    version: int = Field(0, ge=0)  # +1 per committed transaction


class AccountingSummary(IRModel):
    discovered: int
    output: int
    merged: int
    duplicate: int
    excluded: int
    failed: int
    unassigned: int


class Sidecar(DocumentState):
    accounting: AccountingSummary
