"""Base protocol for document providers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from parserx.models.elements import Document


class Provider(Protocol):
    """A provider extracts raw content from a specific document format."""

    def extract(self, path: Path) -> Document:
        """Extract document content from the given file path."""
        ...
