"""Result models for parsing and evaluation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParseResult(BaseModel):
    """Result of parsing a document."""

    markdown: str = ""
    page_count: int = 0
    element_count: int = 0
    api_calls: dict[str, int] = Field(default_factory=dict)  # {"ocr": 5, "vlm": 3, "llm": 1}
    images_total: int = 0
    images_skipped: int = 0
    llm_fallback_hits: int = 0
    warnings: list[str] = Field(default_factory=list)
