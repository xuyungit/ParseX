"""Workspace integrity (plan P2-1, guide §7.3): was the workspace changed only through the tools?

The tools refuse to work on a workspace whose ``state.json`` differs from the
last commit (``Workspace.tampered``).  ``verify_workspace`` is the complete
check an experiment runs afterwards:

- ``state.json`` is what the last transaction committed (``head.json`` and the
  last ``txn`` record agree with its digest and version);
- transactions are numbered 1…N without gaps, and each is claimed by exactly
  one call record — a commit nobody claims came from something other than a
  tool call (e.g. a script using the workspace API directly);
- every asset file still has the digest it was stored under, and the input
  copy still has the source digest (unless it was converted from .doc).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from parserx.ir.base import IRModel
from parserx.workspace.store import Workspace, read_records


class IntegrityReport(IRModel):
    ok: bool
    problems: list[str]
    transactions: int
    calls: int


def verify_workspace(root: Path | str) -> IntegrityReport:
    ws = Workspace.open(root)
    problems: list[str] = []
    problem = ws.tampered()
    if problem is not None:
        problems.append(problem)
    state = ws.load()

    records = read_records(ws.calls_path)
    txns = [r for r in records if r.get("type") == "txn"]
    calls = [r for r in records if r.get("type") == "call"]
    versions = [r.get("version") for r in txns]
    if versions != list(range(1, len(txns) + 1)):
        problems.append(f"transaction versions are not 1…{len(txns)} in order: {versions[:20]}")
    if txns:
        last = txns[-1]
        digest = hashlib.sha256(ws.state_path.read_bytes()).hexdigest()
        if last.get("state_sha256") != digest or last.get("version") != state.version:
            problems.append(f"state.json (version {state.version}) is not what the last transaction "
                            f"(version {last.get('version')}) committed")
    unhashed = [r.get("version") for r in txns if not r.get("state_sha256")]
    if unhashed:
        problems.append(f"transactions without a state digest: {unhashed[:20]}")

    claims: dict[int, int] = {}
    for call in calls:
        for version in call.get("txns", []):
            claims[version] = claims.get(version, 0) + 1
    by_version = {r.get("version"): r for r in txns}
    for version in sorted(by_version):
        n = claims.get(version, 0)
        if n != 1:
            actor = by_version[version].get("actor")
            what = "not claimed by any tool call" if n == 0 else f"claimed by {n} tool calls"
            problems.append(f"transaction version {version} ({actor}) is {what}")
    for version in sorted(set(claims) - set(by_version)):
        problems.append(f"a call record claims transaction version {version}, which does not exist")

    for asset in state.assets:
        path = ws.root / asset.path
        if not path.is_file():
            problems.append(f"asset {asset.id} is missing ({asset.path})")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
            problems.append(f"asset {asset.id} was changed ({asset.path})")
    if not state.source.lower().endswith(".doc"):  # a .doc input is stored as its .docx conversion
        if hashlib.sha256(ws.source_path.read_bytes()).hexdigest() != state.source_sha256:
            problems.append(f"the input copy {ws.source_path.name} was changed")

    return IntegrityReport(ok=not problems, problems=problems, transactions=len(txns), calls=len(calls))
