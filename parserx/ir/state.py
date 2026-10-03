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
from parserx.ir import ids
from parserx.ir.enums import DocumentStatus, ImageRoute, PageStatus, RelationKind
from parserx.ir.evidence import Evidence
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
    "read_text",         # text the local page reading sees where the output had nothing, added by the program (Q133)
]


class PageState(IRModel):
    n: int = Field(ge=1)
    unit: Literal["pdf_page", "docx_segment"]  # DOCX: segments between explicit page / section breaks
    status: PageStatus
    size_pt: tuple[float, float] | None = None  # the page as shown (turned by its /Rotate)
    rotation: Literal[0, 90, 180, 270] = 0  # PDF /Rotate, clockwise; boxes stay in the unrotated page (ir/rotation.py)
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
    reading: list[ReadLine] | None = None  # the local reading of the image (image pixels): checks its text (IO6-5)


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


class ReadRegion(IRModel):
    """A layout-detector region of a read page whose label gives text there a role (page furniture, a footnote)."""

    label: str
    bbox: BBox


class PageReading(IRModel):
    """An independent local reading of one page render (guide §9.5, Q56): evidence for the two-way comparison
    with the output, never output itself."""

    n: int = Field(ge=1)
    engine: str  # recognizer and version
    dpi: int
    lines: list[ReadLine] = []
    not_prose: list[BBox] = []  # layout-detector regions whose text is picture or formula content
    roles: list[ReadRegion] = []  # layout-detector regions of page furniture and footnotes (for added text)


class ClosedItem(IRModel):
    """A worklist signal the agent checked against the page image and left as it is (``close``).  It stays closed
    while the item reads the same (target, kind, quoted text); new content opens it again."""

    target: str
    kind: str
    quotes: list[str] = []
    reason: str
    actor: str
    image: str  # the evidence it was checked on (an evidence id, or an image id of earlier workspaces)
    occluded: bool = False  # text the page draws under another element: content, kept, and named in the summary (Q71)

    @staticmethod
    def quoted(texts: list[str]) -> list[str]:
        """The texts an item quotes: the first five, each at most 80 characters (what a closed item is matched on)."""
        return [t if len(t) <= 80 else t[:79] + "…" for t in texts[:5]]


class Note(IRModel):
    """The agent's understanding of the document, written down (Q87): a revisable interpretation — what a part is,
    which convention holds where — with its scope and the evidence it rests on; separate from the source's facts.
    Notes are revised (``replaces``), never removed: the one no later note replaces is current."""

    id: str  # n-001 …
    text: str
    scope: str  # what it applies to: the whole document, pages 20–35, the appendix …
    evidence: list[str] = []  # evidence ids (view_source)
    replaces: str | None = None  # the note it revises
    actor: str


class Doubt(IRModel):
    """A place for a person to check, never applied to the text: where the original itself may be wrong — a typo, a
    dropped character, a number at odds with the rest (user 2026-10-01) — as the agent raises it (``doubt``), the
    output keeping what the page prints; or a disagreement (``refused``): the agent's correction the program refused
    because the readings of the place show the draft — the original's typo the agent read through, or the readers'
    misreading of a hard place (F, docs §15.3): either way, both versions for a person to compare."""

    id: str  # q-001 …
    block: str
    printed: str  # as the page prints it (and the output has it)
    suggested: str = ""  # what it may be meant as
    reason: str = ""
    by: str  # who took it for a mistake (the agent)
    refused: bool = False  # a disagreement: the agent's correction (``suggested``) refused, the draft (``printed``) kept
    readings: str = ""  # a disagreement: what the readings of the place showed (the refusal's reason)
    evidence: list[str] = []  # evidence ids (view_source)


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
    evidence: list[Evidence] = []  # what was looked at in the source and seen there (Q85)
    notes: list[Note] = []  # the agent's understanding of the document (Q87)
    doubts: list[Doubt] = []  # places the original itself may be wrong, kept as printed
    ledger: list[LedgerEntry] = []
    missing: list[Missing] = []
    stats: Stats = Stats()
    warnings: list[str] = []
    version: int = Field(0, ge=0)  # +1 per committed transaction


def share_containers(state: DocumentState, source: str, parts: list[str]) -> None:
    """Blocks cut from *source* were read where it was: each part is contained by what contains *source* (the
    picture its text was read in), so it still renders and counts as that picture's text (IO6-2)."""
    for src in [r.src for r in state.relations if r.kind == RelationKind.CONTAINS and r.dst == source]:
        state.relations += [Relation(id=ids.relation_id(RelationKind.CONTAINS, src, part), kind=RelationKind.CONTAINS,
                                     src=src, dst=part) for part in parts]


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
