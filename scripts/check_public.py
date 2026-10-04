"""Is what would be pushed fit for the public repository?  (2026-10-04)

ParserX's internal test documents — company documents, personal files, bid materials — live in a private corpus
outside this repository (``PARSERX_PRIVATE_CORPUS``, default ``~/Projects/ParserX-corpus``), linked into
``ground_truth/`` by ``scripts/link_private_corpus.py``.  Before a push this checks the commits being pushed, their
files and messages:

- no string of the private corpus's ``sensitive.txt`` (company, project and person names, report and certificate
  numbers, local paths);
- no key or token (OpenAI-style ``sk-``, AWS, GitHub, Slack, Google, private keys, Bearer tokens);
- no file inside a private document's folder (the folders of the private corpus's ``ground_truth/``).

    python scripts/check_public.py [<revision range>]   # default: what origin/main does not have
    python scripts/check_public.py --hook               # as git's pre-push hook: the refs on stdin

A push that rewrites history (its remote commit is not an ancestor) is checked whole.  Exit 1 with what was found;
without the private corpus the names cannot be checked: said, and only keys and tokens are.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ZERO = "0" * 40
KEYS = {
    "OpenAI-style key": r"\bsk-[A-Za-z0-9_\-]{20,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "GitHub token": r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})",
    "Slack token": r"\bxox[baprs]-[A-Za-z0-9-]{10,}",
    "Google key": r"\bAIza[0-9A-Za-z_\-]{35}\b",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "Bearer token": r"Bearer\s+[A-Za-z0-9._\-]{24,}",
    "key assigned in config": r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-.]{24,}",
}


def private_corpus() -> Path:
    return Path(os.environ.get("PARSERX_PRIVATE_CORPUS", "~/Projects/ParserX-corpus")).expanduser()


def sensitive_strings(corpus: Path) -> list[str]:
    path = corpus / "sensitive.txt"
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]


def private_folders(corpus: Path) -> list[str]:
    root = corpus / "ground_truth"
    return sorted(f"ground_truth/{d.name}/" for d in root.iterdir() if d.is_dir()) if root.is_dir() else []


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, errors="replace",
                          check=True).stdout


def problems(revisions: list[str]) -> list[str]:
    """What the commits of *revisions* (``git log`` arguments) hold that the public repository must not."""
    corpus = private_corpus()
    strings, folders = sensitive_strings(corpus), private_folders(corpus)
    log = _git("log", "-p", "--no-color", "--unified=0", "--format=%x00COMMIT %h%n%B", *revisions)
    found: dict[str, set[str]] = {}
    where = "?"
    for line in log.splitlines():
        if line.startswith("\x00COMMIT "):
            where = f"commit {line[8:]} (message)"
            continue
        if line.startswith("+++ b/"):
            where = line[6:]
            if where.startswith(tuple(folders)):
                found.setdefault("a file of a private document", set()).add(where)
            continue
        if line.startswith(("diff ", "index ", "--- ", "@@", "-", "+++")):
            continue
        text = line[1:] if line.startswith("+") else line
        for name, pattern in KEYS.items():
            if re.search(pattern, text):
                found.setdefault(name, set()).add(where)
        for value in strings:
            if value in text:
                found.setdefault(f"sensitive string {value!r}", set()).add(where)
    out = [f"{what}: {', '.join(sorted(places)[:5])}" for what, places in sorted(found.items())]
    if not strings:
        out_note = f"(no {corpus / 'sensitive.txt'}: names not checked, keys and tokens only)"
        print(out_note, file=sys.stderr)
    return out


def _hook_ranges() -> list[list[str]]:
    ranges = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # a deletion pushes nothing
            continue
        local, remote = parts[1], parts[3]
        rewritten = remote == ZERO or subprocess.run(
            ["git", "merge-base", "--is-ancestor", remote, local], cwd=REPO).returncode != 0
        ranges.append([local] if rewritten else [f"{remote}..{local}"])
    return ranges


def main(argv: list[str]) -> int:
    if argv[:1] == ["--hook"]:
        ranges = _hook_ranges()
    else:
        ranges = [[argv[0]]] if argv else [["origin/main..HEAD"]]
    found = [p for revisions in ranges for p in problems(revisions)]
    if found:
        print("Not fit for the public repository:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
