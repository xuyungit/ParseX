"""The envelope every tool returns (guide §5.2, docs/v2_phase1_interfaces.md §4).

Text that comes from the document is data, never instructions (guide §3.3):
it only ever appears inside ``DocText`` (``{"doc_text": …}``), which runtime
adapters put in a data fence instead of the instruction channel.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import computed_field

from parserx.ir.base import IRModel

R = TypeVar("R")


class DocText(IRModel):
    """Text from the document: data, not instructions."""

    doc_text: str


class BudgetLeft(IRModel):
    requests: dict[str, int] = {}
    usd: float | None = None
    seconds: float | None = None


class Cost(IRModel):
    requests: dict[str, int] = {}  # real network requests: ocr / vlm / llm
    attempts: dict[str, int] = {}
    cache_hits: dict[str, int] = {}
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float | None = None
    wall_s: float = 0.0
    budget_left: BudgetLeft | None = None  # document-level


class FailureCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SERVICE_ERROR = "service_error"
    NOT_CONFIGURED = "not_configured"  # a service without its key or token: the user sets it up (R4)
    TIMEOUT = "timeout"
    CACHE_MISS_OFFLINE = "cache_miss_offline"
    CHECK_FAILED = "check_failed"
    VERSION_CONFLICT = "version_conflict"
    WORKSPACE_TAMPERED = "workspace_tampered"  # state.json changed outside the tools (plan P2-1)
    INTERNAL_ERROR = "internal_error"
    INTERRUPTED = "interrupted"  # the call was stopped (Ctrl-C, deadline): its committed changes stay, claimed


class Failure(IRModel):
    code: FailureCode
    message: str
    retryable: bool
    targets: list[str] = []  # pages ("p3") or blocks affected


class UnresolvedKind(StrEnum):
    PAGE_PENDING = "page_pending"
    BLOCK_FAILED = "block_failed"
    TABLE_MERGE_CANDIDATE = "table_merge_candidate"
    TABLE_ARITHMETIC = "table_arithmetic"  # a product or total that holds in most rows fails in one (P2-7)
    TEXT_SUSPICIOUS = "text_suspicious"  # unreadable characters, or a script found nowhere else (P2-7)
    TEXT_UNACCOUNTED = "text_unaccounted"  # the local page reading sees text no block accounts for (Q56)
    TEXT_ADDED = "text_added"  # text the program added from the local page reading, where the output had none (Q133)
    TEXT_NOT_SEEN = "text_not_seen"  # output text the local page reading does not see where its block sits (Q56)
    NUMBER_UNACCOUNTED = "number_unaccounted"  # a number the local reading sees, not written as one number there (Q148)
    FORMULA_CANDIDATE = "formula_candidate"  # a passage's page reading with formulas, not adopted (Q70)
    READING_DISAGREEMENT = "reading_disagreement"  # a rewrite from the page image no reading confirms: the replaced reading
    SECOND_READING = "second_reading"  # scanned content with mathematics the service model reads otherwise (no text layer)
    ORDER_DISAGREEMENT = "order_disagreement"  # two readings of a page's order disagree across its columns: the other order
    FIGURE_WITHOUT_CONTENT = "figure_without_content"  # a shown image with no description and no text after it
    CAPTION_NUMBER_UNSEEN = "caption_number_unseen"  # a number a picture's description quotes, not read in it (IO6)
    TITLE_LEVEL_UNCLEAR = "title_level_unclear"  # two numbering styles whose nesting the numbers cannot tell (P7)
    TITLE_CANDIDATE = "title_candidate"  # the layout detector sees a section title set apart from the body (D4)
    OUTLINE_REVIEW = "outline_review"  # no title at all, or one-line paragraphs numbered or set like titles that are not
    STRUCTURE_PENDING = "structure_pending"
    BUDGET_SKIPPED = "budget_skipped"
    ASSET_MISSING = "asset_missing"


# items that ask for one look at the whole document, not at a place: closed once, they stay closed
DOCUMENT_ITEMS = frozenset({UnresolvedKind.OUTLINE_REVIEW})


class Unresolved(IRModel):
    target: str
    kind: UnresolvedKind
    detail: str  # written by the program
    quotes: list[DocText] = []  # the document text the item is about: data, not instructions

    @computed_field
    @property
    def id(self) -> str:
        """Stable while the item reads the same (target, kind, quoted text — or its detail when it quotes nothing,
        so that items on one block are told apart, Q164): the name ``dismiss`` takes (Q85).  A document-level item
        (``DOCUMENT_ITEMS``) is one per document and named by its kind alone: what it quotes changes as the agent
        works on it."""
        if self.kind in DOCUMENT_ITEMS:
            return issue_id("", self.kind.value, [])
        return issue_id(self.target, self.kind.value, [q.doc_text for q in self.quotes] or [self.detail])


def issue_id(target: str, kind: str, quotes: list[str]) -> str:
    key = "\x1f".join([target, kind, *quotes])
    return "w-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


class Envelope(IRModel, Generic[R]):
    tool: str
    doc: str
    ws_version: int  # workspace version after the call
    ok: bool  # no fatal failure; per-target failures of a batch are in ``failures`` with ok=True
    result: R | None = None
    cost: Cost = Cost()
    failures: list[Failure] = []


class ToolFailure(Exception):
    """Raised inside a tool for a fatal, request-level failure (the envelope gets ok=False)."""

    def __init__(self, code: FailureCode, message: str, *, retryable: bool = False, targets: list[str] | None = None):
        super().__init__(message)
        self.failure = Failure(code=code, message=message, retryable=retryable, targets=targets or [])
