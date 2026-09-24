"""Skills (guide §5.4): task guidance for runtimes, kept as Markdown files in the repository.

The method lives here, not in any agent's session history; the program still
enforces data, legality and budget constraints.  A skill's content hash takes
part in cache keys wherever its text is sent to a model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent
SKILLS = ("transcription", "figure", "structure")


@dataclass(frozen=True)
class SkillText:
    name: str
    text: str
    sha256: str


def load_skill(name: str, directory: Path | None = None) -> SkillText:
    if name not in SKILLS:
        raise KeyError(f"no skill {name!r}; known: {', '.join(SKILLS)}")
    text = ((directory or _DIR) / f"{name}.md").read_text(encoding="utf-8")
    return SkillText(name=name, text=text, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
