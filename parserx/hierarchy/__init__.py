"""Document structure (guide §6.8). Phase 1: structure changes and their legality checks."""

from parserx.hierarchy.changes import (
    AddRelation,
    ApplyOutcome,
    LegalityRule,
    MarkPending,
    MergeTables,
    MoveAfter,
    Rejection,
    RemoveRelation,
    SetLevel,
    SetRole,
    StructuralKind,
    StructureChange,
)
from parserx.hierarchy.legality import apply_changes, check_changes, numbering_signature

__all__ = [
    "AddRelation", "ApplyOutcome", "LegalityRule", "MarkPending", "MergeTables", "MoveAfter", "Rejection", "RemoveRelation",
    "SetLevel", "SetRole", "StructuralKind", "StructureChange", "apply_changes", "check_changes",
    "numbering_signature",
]
