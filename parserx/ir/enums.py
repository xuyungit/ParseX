"""IR enumerations (docs/v2_phase1_interfaces.md §2.2). States are enums, never flags (guide §4.2)."""

from __future__ import annotations

from enum import StrEnum


class BlockKind(StrEnum):
    TITLE = "title"
    TEXT = "text"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    WATERMARK = "watermark"
    FOOTNOTE = "footnote"
    SCAN = "scan"
    OTHER = "other"


class BlockStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    EXCLUDED = "excluded"
    MERGED = "merged"
    DUPLICATE = "duplicate"


class ObservationStatus(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"
    SKIPPED_BUDGET = "skipped_budget"


class RelationKind(StrEnum):
    CONTAINS = "contains"
    FOLLOWS = "follows"
    CONTINUES = "continues"
    CAPTIONS = "captions"
    FOOTNOTES = "footnotes"
    BELONGS_TO_SECTION = "belongs_to_section"
    DUPLICATE_OF = "duplicate_of"


class DecisionStage(StrEnum):
    IMAGE_ROUTE = "image_route"
    CONTENT_SOURCE = "content_source"
    REVIEW_ACCEPT = "review_accept"
    HEADING_ROLE = "heading_role"
    HEADING_LEVEL = "heading_level"
    EXCLUDE = "exclude"
    BUDGET = "budget"
    STRUCTURE = "structure"  # reading order, relations, "structure pending" (apply_structure)


class TaskKind(StrEnum):
    """Which task produced an Observation (guide §6.1, §8.1)."""

    EXTRACT = "extract"
    LAYOUT = "layout"
    RECOGNIZE = "recognize"
    REVIEW = "review"
    CORRECT = "correct"  # the agent's own reading of an image it looked at (P2-5, Q30)
    DESCRIBE = "describe"
    EXPLAIN = "explain"


class EvidenceLevel(StrEnum):
    VISIBLE = "visible"
    ESTIMATED = "estimated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class ImageRoute(StrEnum):
    SCAN = "SCAN"
    FIGURE = "FIGURE"
    MIXED = "MIXED"
    UNCERTAIN = "UNCERTAIN"
    DECORATIVE = "DECORATIVE"


class PageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
