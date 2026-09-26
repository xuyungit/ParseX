"""``Pipeline``: parse one document with the fixed-sequence runtime (guide §3.1, §7).

The workspace + toolkit core runs the standard processing and exports the output package
(``runtimes/pipeline.py``).  The class keeps the names the evaluation and tool comparison call; the v1 processor
pipeline it used to run was removed in Phase 5 (Q74; the local tag ``v1-final`` keeps it).
"""

from __future__ import annotations

from pathlib import Path

from parserx.config.schema import ParserXConfig, load_config
from parserx.models.results import ParseResult


class Pipeline:
    def __init__(self, config: ParserXConfig | None = None):
        self._config = config if config is not None else load_config()

    def parse(self, path: str | Path) -> str:
        """Parse a document and return its Markdown."""
        return self.parse_result(path).markdown

    def parse_result(self, path: str | Path) -> ParseResult:
        """Parse a document: the Markdown, the sidecar and the request counts."""
        from parserx.runtimes.pipeline import parse_result

        return parse_result(_existing(path), self._config)

    def parse_to_dir(self, path: str | Path, output_dir: str | Path) -> Path:
        """Parse a document into the output package in *output_dir*; the path of its Markdown."""
        return self.parse_result_to_dir(path, output_dir).markdown_path

    def parse_result_to_dir(self, path: str | Path, output_dir: str | Path) -> ParseResult:
        """Parse a document into the output package in *output_dir* (guide §4.5, Q42)."""
        from parserx.runtimes.pipeline import parse_to_dir

        return parse_to_dir(_existing(path), output_dir, self._config)


def _existing(path: str | Path) -> Path:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    return path
