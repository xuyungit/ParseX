"""Document structure (guide §6.8). Phase 1: structure changes and their legality checks."""

from parserx.hierarchy.changes import (
    Link,
    ApplyOutcome,
    Exclude,
    LegalityRule,
    MarkPending,
    MergeTables,
    Move,
    Rejection,
    Unlink,
    Include,
    SetLevel,
    SetRole,
    Split,
    StructuralKind,
    StructureChange,
)
from parserx.hierarchy.legality import apply_changes, check_changes, numbering_signature

__all__ = [
    "Link", "ApplyOutcome", "Exclude", "LegalityRule", "MarkPending", "MergeTables", "Move", "Rejection", "Unlink",
    "Include", "SetLevel", "SetRole", "Split", "StructuralKind", "StructureChange", "apply_changes", "check_changes",
    "numbering_signature",
]
