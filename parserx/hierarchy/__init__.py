"""Document structure (guide §6.8): structure changes and their legality checks."""

from parserx.hierarchy.changes import (
    ApplyOutcome,
    Exclude,
    Include,
    Join,
    LegalityRule,
    MarkPending,
    Move,
    Rejection,
    Role,
    SetRole,
    Split,
    StructureChange,
    Unjoin,
)
from parserx.hierarchy.legality import apply_batch, apply_changes, check_changes, numbering_signature

__all__ = [
    "ApplyOutcome", "Exclude", "Include", "Join", "LegalityRule", "MarkPending", "Move", "Rejection", "Role", "SetRole",
    "Split", "StructureChange", "Unjoin", "apply_batch", "apply_changes", "check_changes", "numbering_signature",
]
