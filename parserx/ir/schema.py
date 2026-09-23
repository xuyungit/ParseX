"""JSON Schema of the sidecar (``<name>.blocks.json``, guide §4.5).

Consumers validate sidecars against this schema without importing ParserX;
``validate_sidecar`` uses a standard JSON Schema validator for the same reason.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from parserx.ir.state import Sidecar


def sidecar_json_schema() -> dict[str, Any]:
    return Sidecar.model_json_schema(mode="serialization")


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = sidecar_json_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_sidecar(data: Any) -> list[str]:
    """Schema violations as readable strings; empty when *data* is a valid sidecar."""
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in errors]
