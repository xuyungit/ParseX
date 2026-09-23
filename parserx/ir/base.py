"""Shared base for v2 IR models (guide §4, docs/v2_phase1_interfaces.md §2.1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1
# a b c d e f (PDF matrix convention): x' = a·x + c·y + e,  y' = b·x + d·y + f
Affine = tuple[float, float, float, float, float, float]


class IRModel(BaseModel):
    """Unknown fields are errors: state lives in typed fields, never in free dicts or flags."""

    # Serialization schemas (the sidecar) mark defaulted fields required: exports always emit them.
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, json_schema_serialization_defaults_required=True
    )
