"""Link the private corpus's documents into ``ground_truth/`` (2026-10-04).

ParserX's internal test documents (company documents, personal files, bid materials) are not in this public
repository: they live in a private corpus (``PARSERX_PRIVATE_CORPUS``, default ``~/Projects/ParserX-corpus``,
its documents under ``ground_truth/<doc>/``).  Each of them is linked here as ``ground_truth/<doc>``, so the
evaluation, the replays and every script find the public and the private documents side by side; git ignores the
links (``ground_truth/*`` is ignored but for the public annotations).

    python scripts/link_private_corpus.py           # make the missing links, say what is in the way
    python scripts/link_private_corpus.py --hook    # also install the pre-push check (scripts/check_public.py)

A folder of a private document's name that is not a link is left alone and named: move it into the private corpus
first.  Without the private corpus, the public documents alone are there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOOK = """#!/bin/sh
# ParserX: refuse a push that holds internal content (scripts/check_public.py)
exec uv run --frozen python scripts/check_public.py --hook
"""


def main(argv: list[str]) -> int:
    corpus = Path(os.environ.get("PARSERX_PRIVATE_CORPUS", "~/Projects/ParserX-corpus")).expanduser()
    source = corpus / "ground_truth"
    if not source.is_dir():
        print(f"no private corpus at {source}: the public documents only")
        return 0
    made, kept, blocked = 0, 0, []
    for doc in sorted(d for d in source.iterdir() if d.is_dir()):
        link = REPO / "ground_truth" / doc.name
        if link.is_symlink():
            kept += 1
        elif link.exists():
            blocked.append(doc.name)
        else:
            link.symlink_to(doc)
            made += 1
    print(f"{made} linked, {kept} linked before, from {source}")
    if blocked:
        print("not links (move them into the private corpus first): " + ", ".join(blocked))
    if "--hook" in argv:
        hook = REPO / ".git" / "hooks" / "pre-push"
        hook.write_text(HOOK, encoding="utf-8")
        hook.chmod(0o755)
        print(f"pre-push check installed: {hook}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
