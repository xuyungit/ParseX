"""What an extractor hands to ``workspace init``: blocks, ledger, assets, page states."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from parserx.ir.asset import Asset
from parserx.ir.block import Block
from parserx.ir.enums import DocumentStatus
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry, Missing, PageState


@dataclass
class Extraction:
    format: Literal["pdf", "docx"]
    engines: dict[str, str]
    pages: list[PageState] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    asset_bytes: dict[str, bytes] = field(default_factory=dict)  # asset path → bytes
    warnings: list[str] = field(default_factory=list)
    missing: list[Missing] = field(default_factory=list)  # content found but not extracted (failed blocks)

    def add_asset(self, asset: Asset, data: bytes) -> Asset:
        """Record *asset* once (content-addressed: the same bytes are one asset)."""
        if asset.path not in self.asset_bytes:
            self.assets.append(asset)
            self.asset_bytes[asset.path] = data
        return next(a for a in self.assets if a.path == asset.path)

    def to_state(self, *, doc_id: str, source: str, source_sha256: str) -> DocumentState:
        return DocumentState(
            id=doc_id, source=source, source_sha256=source_sha256, format=self.format,
            status=DocumentStatus.IN_PROGRESS, engines=dict(sorted(self.engines.items())),
            pages=self.pages, blocks=self.blocks, relations=self.relations, assets=self.assets,
            ledger=self.ledger, missing=self.missing, warnings=self.warnings,
        )
