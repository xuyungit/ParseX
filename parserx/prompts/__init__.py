"""Prompts of the VLM tasks inside the tools (guide §5.4, §8.3).

Kept apart from the runtime-facing Skills.  A prompt's content hash is part of
every cache key that uses it and is recorded in ``DocumentState.prompt_hashes``,
so editing a prompt invalidates exactly the responses it produced.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_prompt(name: str) -> tuple[str, str]:
    """(text, sha256) of ``parserx/prompts/<name>.md``."""
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()
