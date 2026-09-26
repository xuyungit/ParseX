#!/usr/bin/env python3
"""P5-2: the fixed pipeline on the whole corpus, offline, against a baseline run — headings first, information kept.

Runs the working tree's fixed pipeline from a frozen run's response cache (read-only: a request the cache lacks
fails the document instead of calling a service), then compares per document with the baseline outputs and v1:
heading_f1 and role F1 (means over the documents v1 has scores for, and over all), and char_f1 / table F1 moves
beyond the tolerance.

    uv run python scripts/heading_compare.py --out /tmp/p5-2/try1
    uv run python scripts/heading_compare.py --out /tmp/p5-2/try1 --no-run   # compare only
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from phase4_compare import GT_DIRS, TOLERANCE, V1, scores  # noqa: E402

BASELINE = REPO_ROOT / "eval_runs" / "2026-09-26_p4-7b_v2_fixed_full"


def run(out: Path, cache_run: Path) -> None:
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "regression_test.py"),
           "--config", str(REPO_ROOT / "configs" / "regression_v2.yaml"),
           *[a for gt in GT_DIRS for a in ("--gt-dir", str(gt))],
           "--cache-mode", "read_only", "--cache-dir", str(cache_run / "cache"),
           "--outputs-dir", str(out), "--json-out", str(out / "result.json")]
    done = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    print(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr[-2000:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="outputs directory of this run")
    ap.add_argument("--baseline", type=Path, default=BASELINE / "outputs", help="outputs to compare with")
    ap.add_argument("--cache-run", type=Path, default=BASELINE, help="frozen run whose cache is replayed")
    ap.add_argument("--no-run", action="store_true", help="only compare existing outputs")
    ap.add_argument("--all", action="store_true", help="list every document, not only those that moved")
    args = ap.parse_args()
    if not args.no_run:
        args.out.mkdir(parents=True, exist_ok=True)
        run(args.out, args.cache_run)

    rows = []
    for gt in GT_DIRS:
        for d in sorted(gt.iterdir()):
            if (d / "expected.md").is_file():
                expected = (d / "expected.md").read_text(encoding="utf-8")
                rows.append((d.name, {k: scores(p / f"{d.name}.md", expected, d.name) for k, p in
                                      (("v1", V1 / "outputs"), ("base", args.baseline), ("new", args.out))}))

    def f(v):
        return "  —  " if v is None else f"{v:.3f}"

    print(f"{'document':42} {'heading v1/base/new':>22} {'role v1/base/new':>22}  information")
    for name, s in rows:
        heading = [(s[k] or {}).get("heading_f1") for k in ("v1", "base", "new")]
        role = [(s[k] or {}).get("role_f1") for k in ("v1", "base", "new")]
        info = []
        for m in ("char_f1", "table_f1"):
            a, b = (s["base"] or {}).get(m), (s["new"] or {}).get(m)
            if a is not None and b is not None and abs(b - a) > TOLERANCE:
                info.append(f"{m} {a:.3f}→{b:.3f}")
            elif (a is None) != (b is None):
                info.append(f"{m} {f(a)}→{f(b)}")
        if args.all or heading[1] != heading[2] or role[1] != role[2] or info:
            print(f"{name[:42]:42} {' '.join(map(f, heading)):>22} {' '.join(map(f, role)):>22}  {'; '.join(info)}")
    print()
    for label, keys in (("with v1 scores", ("v1", "base", "new")), ("all scored", ("base", "new"))):
        for m in ("heading_f1", "role_f1"):
            both = [s for _, s in rows if all((s[k] or {}).get(m) is not None for k in keys)]
            means = " · ".join(f"{k} {sum(s[k][m] for s in both) / len(both):.3f}" for k in keys)
            print(f"{m:10s} {label:15s} {means}  (n={len(both)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
