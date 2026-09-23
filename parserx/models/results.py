"""Result models for parsing and evaluation."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ParseResult(BaseModel):
    """Result of parsing a document."""

    markdown: str = ""
    markdown_path: Path | None = None
    page_count: int = 0
    element_count: int = 0
    # Real requests recorded at the service boundary: {"ocr": 1, "vlm": 3, "llm": 0}
    api_calls: dict[str, int] = Field(default_factory=dict)
    api_attempts: dict[str, int] = Field(default_factory=dict)
    ocr_pages: int = 0  # pages submitted to OCR; one batch request can carry many
    cache_hits: dict[str, int] = Field(default_factory=dict)
    # Offline replay only: responses the cache lacked (the document is not replayable).
    cache_misses: dict[str, int] = Field(default_factory=dict)
    images_total: int = 0
    images_skipped: int = 0
    llm_fallback_hits: int = 0
    page_types: dict[str, int] = Field(default_factory=dict)  # {"native": 8, "scanned": 2, "mixed": 1}
    warnings: list[str] = Field(default_factory=list)
