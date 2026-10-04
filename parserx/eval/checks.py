"""Must-read checks for a document without a full annotation (Q154): places that have to come out right — company
names, report and certificate numbers, clause numbers, dates, amounts — each verified on the original.

``checks.json`` beside the document::

    {"checks": [{"id": "c01", "text": "…as printed…", "where": "balance sheet, header", "min": 1},
                {"id": "x01", "text": "…a known misreading…", "absent": true}]}

A check passes when its text occurs in the output at least ``min`` times (default 1), or, with ``absent``, not at
all.  Text is compared with markup, line breaks and spaces removed and both sides NFKC-normalised, so a check tests
the characters, not how the Markdown lays them out.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel

_MARKUP = re.compile(r"[\s*_`|>#\\]+")


class Check(BaseModel):
    id: str
    text: str
    where: str = ""
    min: int = 1
    absent: bool = False


class CheckResult(BaseModel):
    id: str
    text: str
    where: str
    found: int
    passed: bool


def plain(text: str) -> str:
    return _MARKUP.sub("", unicodedata.normalize("NFKC", text))


def load(path: Path) -> list[Check]:
    return [Check(**c) for c in json.loads(path.read_text(encoding="utf-8"))["checks"]]


def run_checks(markdown: str, checks: list[Check]) -> list[CheckResult]:
    body = plain(markdown)
    results = []
    for check in checks:
        found = body.count(plain(check.text))
        passed = found == 0 if check.absent else found >= check.min
        results.append(CheckResult(id=check.id, text=check.text, where=check.where, found=found, passed=passed))
    return results
