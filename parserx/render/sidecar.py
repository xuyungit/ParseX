"""Sidecar export (``<name>.blocks.json``, guide §4.5): the state plus its accounting summary."""

from __future__ import annotations

import json
from typing import Any

from parserx.accounting import check
from parserx.ir.state import DocumentState, Sidecar


def export_sidecar(state: DocumentState) -> dict[str, Any]:
    sidecar = Sidecar(**state.model_dump(), accounting=check(state).accounting)
    return sidecar.model_dump(mode="json")


def sidecar_json(state: DocumentState) -> str:
    """Deterministic text: the same state gives the same bytes (``stats`` is the only run-dependent part)."""
    return json.dumps(export_sidecar(state), ensure_ascii=False, indent=1) + "\n"
