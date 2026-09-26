#!/usr/bin/env python3
"""P4-7: v1 (frozen), the fixed pipeline (a frozen full run) and the hybrid runtime (``agent_explore.py parse``
records) per document, with the Phase 4 exit checks.

    uv run python scripts/phase4_compare.py --fixed eval_runs/<run> --hybrid ~/parserx-exp/phase4/p4c/_parse
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from parserx.eval.metrics import _extract_headings, _normalize_heading, evaluate_markdown  # noqa: E402

V1 = REPO_ROOT / "eval_runs" / "2026-09-23_p0_v1_gpt-6-luna"
GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public")
TOLERANCE = 0.005  # the exit condition's (plan §0)


def role_f1(out: str, exp: str) -> float | None:
    """Title text found, whatever its level (the heading metric's text matching)."""
    d = [_normalize_heading(t) for _, t in _extract_headings(out)]
    e = [_normalize_heading(t) for _, t in _extract_headings(exp)]
    if not e:
        return None if not d else 0.0
    used, hit = set(), 0
    for x in d:
        for i, y in enumerate(e):
            if i not in used and (x == y or x in y or y in x):
                used.add(i)
                hit += 1
                break
    p, r = hit / max(len(d), 1), hit / len(e)
    return round(2 * p * r / max(p + r, 1e-10), 4)


def scores(md: Path | None, expected: str, name: str) -> dict | None:
    if md is None or not md.is_file():
        return None
    out = md.read_text(encoding="utf-8")
    r = evaluate_markdown(out, expected, name=name)
    return {"char_f1": r.text.char_f1, "table_f1": r.tables.cell_f1,
            "heading_f1": r.headings.f1, "role_f1": role_f1(out, expected)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed", type=Path, required=True, help="frozen full run of the fixed pipeline")
    ap.add_argument("--hybrid", type=Path, required=True, help="directory of agent_explore.py parse runs")
    ap.add_argument("--json-out", type=Path)
    args = ap.parse_args()
    rows = []
    for gt in GT_DIRS:
        for d in sorted(gt.iterdir()):
            if not (d / "expected.md").is_file():
                continue
            expected = (d / "expected.md").read_text(encoding="utf-8")
            record = args.hybrid / d.name / "record.json"
            rec = json.loads(record.read_text()) if record.is_file() else None
            hybrid_md = Path(rec["outcome"]["markdown"]) if rec and rec.get("outcome") else None
            outcome = (rec or {}).get("outcome") or {}
            agent = outcome.get("agent") or {}
            rows.append({
                "doc": d.name,
                "v1": scores(V1 / "outputs" / f"{d.name}.md", expected, d.name),
                "fixed": scores(args.fixed / "outputs" / f"{d.name}.md", expected, d.name),
                "hybrid": scores(hybrid_md, expected, d.name),
                "runtime": outcome.get("runtime"), "note": outcome.get("runtime_note"),
                "wall_s": (rec or {}).get("wall_s"), "agent_usd": agent.get("usd_at_list_price"),
                "service_usd": outcome.get("service_usd"), "open_before": agent.get("review_open_before"),
                "open_after": outcome.get("review_open"),
                "hygiene": None if rec is None else ((rec.get("audit") or {"ok": True})["ok"]
                                                    and (rec.get("integrity") or {"ok": True})["ok"]),
            })

    def f(v):
        return "—" if v is None else f"{v:.3f}"

    print("| 文档 | 运行时 | char v1 / 固定 / 混合 | 表格 v1 / 固定 / 混合 | 标题 v1 / 固定 / 混合 | 角色 v1 / 固定 / 混合 "
          "| 待核对 前→后 | 用时 | Agent 标价 | 卫生 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        cells = []
        for m in ("char_f1", "table_f1", "heading_f1", "role_f1"):
            cells.append(" / ".join(f((r[k] or {}).get(m)) for k in ("v1", "fixed", "hybrid")))
        print(f"| {r['doc']} | {r['runtime'] or '—'} | " + " | ".join(cells)
              + f" | {r['open_before'] if r['open_before'] is not None else '—'}→{r['open_after'] if r['open_after'] is not None else '—'}"
              f" | {f(r['wall_s']) if r['wall_s'] else '—'} s | {f(r['agent_usd'])} | "
              f"{'—' if r['hygiene'] is None else ('✅' if r['hygiene'] else '❌')} |")
    print()
    for m in ("char_f1", "table_f1", "heading_f1", "role_f1"):
        both = [r for r in rows if all((r[k] or {}).get(m) is not None for k in ("v1", "fixed", "hybrid"))]
        if both:
            means = [sum(r[k][m] for r in both) / len(both) for k in ("v1", "fixed", "hybrid")]
            print(f"{m:10s} v1 {means[0]:.3f} · fixed {means[1]:.3f} · hybrid {means[2]:.3f}  (n={len(both)})")
    print("\ninformation below v1 by more than the tolerance (hybrid):")
    for r in rows:
        for m in ("char_f1", "table_f1"):
            a, b = (r["v1"] or {}).get(m), (r["hybrid"] or {}).get(m)
            if a is not None and b is not None and b < a - TOLERANCE:
                print(f"  {r['doc']} {m} {a:.3f} → {b:.3f}")
    cost = sum(r["agent_usd"] or 0 for r in rows)
    print(f"\nagent runs {sum(1 for r in rows if r['runtime'] == 'hybrid:agent')} · fallbacks "
          f"{sum(1 for r in rows if r['runtime'] == 'hybrid:fallback')} · agent at list price ${cost:.2f} · "
          f"services ${sum(r['service_usd'] or 0 for r in rows):.2f} · hygiene failures "
          f"{sum(1 for r in rows if r['hygiene'] is False)}")
    if args.json_out:
        args.json_out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
