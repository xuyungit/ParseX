"""Cache keys: a hash over the complete request semantics (guide §8.3).

A key covers everything that can change a response — service, endpoint
identity, model and generation settings, prompt texts, the *bytes* of every
image or file sent, structured-output schema — and nothing that cannot:
credentials, temp-file paths, timeouts.  ``CACHE_SCHEMA_VERSION`` invalidates
every entry when the key layout itself changes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from parserx.config.schema import ServiceConfig, effort_for

CACHE_SCHEMA_VERSION = 1


def request_key(service: str, material: dict[str, Any]) -> str:
    blob = json.dumps(
        {"cache_schema": CACHE_SCHEMA_VERSION, "service": service, **material},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def bytes_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def endpoint_identity(url: str) -> str:
    """Host and path only: no scheme differences, credentials or query strings."""
    parts = urlsplit(url or "")
    return f"{parts.hostname or ''}{parts.path.rstrip('/')}"


def service_identity(config: ServiceConfig) -> dict[str, Any]:
    """The parts of an LLM/VLM service config that shape its responses: the effort as sent (Q100: the nearest the
    model accepts), and the strongest structured output only where the model's entry limits it below what the
    tasks ask, so the keys of configurations that do not limit it stay as they were."""
    identity = {
        "endpoint": endpoint_identity(config.endpoint),
        "model": config.model,
        "api_style": config.api_style,
        "reasoning_effort": effort_for(config.reasoning_effort, config.efforts),
        "send_temperature": config.send_temperature,
        "min_output_tokens": config.min_output_tokens,
        "extra_body": config.extra_body,
    }
    if config.structured_output not in (None, "json_schema"):  # weaker than what the tasks ask: the schema is in
        identity["structured_output"] = {"mode": config.structured_output, "schema_in_prompt": True}  # the prompt
    return identity


def digest_arguments(value: Any) -> Any:
    """Replace file paths by the digest of their bytes, recursively."""
    if isinstance(value, Path):
        return {"file_sha256": bytes_digest(value.read_bytes())}
    if isinstance(value, (list, tuple)):
        return [digest_arguments(v) for v in value]
    if isinstance(value, dict):
        return {k: digest_arguments(v) for k, v in value.items()}
    return value
