"""Structural validation for processed documents and chapter outputs."""

from __future__ import annotations

from pathlib import Path

from parserx.models.elements import Document


class StructureValidator:
    """Validate heading hierarchy and chapter output integrity."""

    def validate(
        self,
        doc: Document,
        chapter_dir: Path | None = None,
    ) -> list[str]:
        warnings = self._validate_headings(doc)
        if chapter_dir is not None:
            warnings.extend(self.validate_chapter_files(chapter_dir))
        return warnings

    def _validate_headings(self, doc: Document) -> list[str]:
        warnings: list[str] = []
        active_levels: dict[int, str] = {}
        previous_level = 0

        for page in doc.pages:
            for elem in page.elements:
                level = elem.metadata.get("heading_level")
                if not level:
                    continue

                title = elem.content.split("\n")[0].strip()
                if not title:
                    warnings.append(
                        f"Page {page.number}: empty heading detected at level H{level}."
                    )
                    continue

                if previous_level and level > previous_level + 1:
                    warnings.append(
                        f"Page {page.number}: heading level jump from H{previous_level} to H{level} ({title})."
                    )

                if level > 1 and (level - 1) not in active_levels:
                    warnings.append(
                        f"Page {page.number}: orphan H{level} heading without H{level - 1} parent ({title})."
                    )

                active_levels = {
                    existing_level: existing_title
                    for existing_level, existing_title in active_levels.items()
                    if existing_level < level
                }
                active_levels[level] = title
                previous_level = level

        return warnings

    def validate_chapter_files(self, output_dir: Path) -> list[str]:
        warnings: list[str] = []
        chapters_dir = output_dir / "chapters"
        if not chapters_dir.exists():
            return warnings

        for chapter_file in sorted(chapters_dir.glob("ch_*.md")):
            if not chapter_file.read_text(encoding="utf-8").strip():
                warnings.append(f"Chapter file is empty: {chapter_file.name}.")

        return warnings
