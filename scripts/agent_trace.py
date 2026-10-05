"""Where an agent run's time went, step by step (speed plan: the agent's trajectory).

    uv run python scripts/agent_trace.py WORK_DIR [--out REPORT.md] [--json OUT.json]

WORK_DIR is a ``parserx parse --keep-work`` work directory (``.parserx-work``): the agent's events
(``agent_run/events.jsonl`` with the arrival times ``events.N.times.json`` for Codex, ``agent_run/trace.jsonl`` for our
own loop) and the workspace's call log (``agent/ws/calls.jsonl``).  The report has a timeline — for every action the
model time before it, what it was (a tool and its view or ops, a script, another command), the tool's own seconds,
how much it returned and how it ended — and a summary: time by kind of action, failed and refused calls, repeated
requests, scripts the agent wrote to read the tools' output, and what became of each kind of review item.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

_PX_TOOL = re.compile(r"\./px\s+tool\s+(\w+)")


def _calls(ws: Path) -> list[dict]:
    path = ws / "calls.jsonl"
    if not path.is_file():
        return []
    return [r for r in map(json.loads, path.read_text().splitlines()) if r.get("type") == "call"]


def _summary_of(call: dict) -> str:
    req = call.get("request") or {}
    tool = call["tool"]
    if tool == "read_draft":
        parts = [req.get("view") or ""]
        if req.get("blocks"):
            parts.append(f"{len(req['blocks'])} blocks")
        for key in ("page", "cls", "find", "pattern"):
            if req.get(key):
                parts.append(f"{key}={str(req[key])[:20]}")
        if req.get("full"):
            parts.append("full")
        return " ".join(p for p in parts if p)
    if tool == "view_source":
        looks = req.get("looks") or []
        return f"{len(looks)} looks: " + ", ".join(f"{lk.get('as')}" for lk in looks[:6])
    if tool == "edit_draft":
        ops = Counter(o.get("op") for o in req.get("ops") or [])
        return ", ".join(f"{k}×{v}" for k, v in ops.most_common())
    return ""


def _outcome(call: dict) -> tuple[str, list[str]]:
    """(outcome label, problems): failed / refused ops / ok."""
    env = call.get("envelope") or {}
    problems = [f"{f.get('code')}: {f.get('message', '')[:140]}" for f in env.get("failures") or []]
    if not env.get("ok", True):
        return "failed", problems
    result = call.get("result") or {}
    if call["tool"] == "edit_draft" and isinstance(result, dict):
        outcomes = result.get("outcomes") or []
        refused = [o for o in outcomes if not o.get("accepted")]
        problems += [f"refused {o.get('op')}: {o.get('rule')}: {(o.get('detail') or '')[:140]}" for o in refused]
        if refused:
            return f"{len(outcomes) - len(refused)}/{len(outcomes)} accepted", problems
        return f"{len(outcomes)} accepted", problems
    if call["tool"] == "submit_draft" and isinstance(result, dict):
        return ("accepted" if result.get("accepted") else "refused"), problems
    return ("ok with failures" if problems else "ok"), problems


def _codex_actions(run: Path) -> list[dict]:
    """Commands and messages of a Codex session with when each started and ended (seconds from the start)."""
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines() if line.strip()]
    times: list[float] = []
    for k in range(1, 20):
        f = run / f"events.{k}.times.json"
        if f.is_file():
            times += json.loads(f.read_text())
    if len(times) != len(events):
        raise SystemExit(f"{len(events)} events but {len(times)} arrival times (a run before the times were kept)")
    t0 = times[0]
    started: dict[str, float] = {}
    actions, last_end = [], 0.0
    for event, t in zip(events, times):
        t -= t0
        item = event.get("item") or {}
        if event.get("type") == "item.started" and item.get("type") == "command_execution":
            started[item["id"]] = t
        elif event.get("type") == "item.completed":
            kind = item.get("type")
            begin = started.get(item.get("id"), t)
            entry = {"start": round(begin, 1), "end": round(t, 1), "wait": round(max(0.0, begin - last_end), 1),
                     "kind": kind}
            if kind == "command_execution":
                command = item.get("command", "")
                tool = _PX_TOOL.search(command)
                entry.update(action=f"tool {tool.group(1)}" if tool else ("script" if "./px python" in command
                                                                          else "shell"),
                             command=command[command.find("./px"):][:160] if "./px" in command else command[:160],
                             returned=len(item.get("aggregated_output") or ""), exit=item.get("exit_code"),
                             seconds=round(t - begin, 1))
            elif kind in ("agent_message", "reasoning"):
                entry.update(action="message", command=(item.get("text") or "")[:160].replace("\n", " "),
                             returned=0, seconds=0.0)
            else:
                entry.update(action=str(kind), command="", returned=0, seconds=round(t - begin, 1))
            actions.append(entry)
            last_end = max(last_end, t)
    return actions


def _loop_actions(run: Path) -> list[dict]:
    """Our loop's model turns and tool calls (trace.jsonl), on one clock."""
    actions, clock = [], 0.0
    for record in map(json.loads, (run / "trace.jsonl").read_text().splitlines()):
        wait = float(record.get("model_s") or 0.0)
        clock += wait
        calls = record.get("calls") or []
        context = (record.get("usage") or {}).get("input_tokens")
        if not calls:
            actions.append({"start": round(clock, 1), "end": round(clock, 1), "wait": round(wait, 1), "kind": "message",
                            "action": "message", "command": f"{record.get('text_chars', 0)} chars", "returned": 0,
                            "seconds": 0.0})
        for n, call in enumerate(calls):
            s = float(call.get("s") or 0.0)
            actions.append({"start": round(clock, 1), "end": round(clock + s, 1), "wait": round(wait if n == 0 else 0.0, 1),
                            "kind": "call", "action": f"tool {call.get('name')}",
                            "command": str(call.get("arguments"))[:160], "returned": call.get("result_chars", 0),
                            "images": call.get("images", 0), "seconds": round(s, 1),
                            "context": context if n == 0 else None})
            clock += s
    return actions


def analyse(work: Path) -> dict:
    run, ws = work / "agent_run", work / "agent" / "ws"
    engine = "codex" if (run / "events.jsonl").is_file() else "loop"
    actions = _codex_actions(run) if engine == "codex" else _loop_actions(run)
    calls = _calls(ws)
    # px tool commands and loop calls in order ↔ the call log's records in order (one record per tool call)
    pending = list(calls)
    seen: Counter = Counter()
    for a in actions:
        if not a["action"].startswith("tool "):
            continue
        name = a["action"][5:]
        match = next((c for c in pending if c["tool"] == name), None)
        if match is None:
            a["outcome"], a["problems"] = "no call record (refused before the tool ran)", []
            continue
        pending.remove(match)
        a["summary"] = _summary_of(match)
        a["outcome"], a["problems"] = _outcome(match)
        a["tool_s"] = (match.get("envelope") or {}).get("cost", {}).get("wall_s")
        key = (name, json.dumps(match.get("request"), sort_keys=True))
        seen[key] += 1
        a["repeat"] = seen[key] > 1
        a["call"] = match
    return {"engine": engine, "actions": actions, "items": _items(calls)}


def _items(calls: list[dict]) -> dict:
    """Review items by kind: listed (first issues view), closed (dismiss), changed near them (accepted edits)."""
    listed: dict[str, str] = {}
    for c in calls:
        if c["tool"] == "read_draft" and (c.get("request") or {}).get("view") == "issues":
            issues = (c.get("result") or {}).get("issues") or []
            for item in issues if isinstance(issues, list) else issues.get("items", []):
                listed.setdefault(item.get("id"), item.get("kind"))
    closed: Counter = Counter()
    for c in calls:
        if c["tool"] != "edit_draft":
            continue
        ops = (c.get("request") or {}).get("ops") or []
        for o in ((c.get("result") or {}).get("outcomes") or []):
            op = ops[o["index"]] if o.get("index", -1) < len(ops) else {}
            if op.get("op") == "dismiss" and o.get("accepted"):
                closed[listed.get(op.get("issue"), "?")] += 1
    return {"listed": dict(Counter(listed.values())), "closed": dict(closed)}


def report(work: Path, result: dict) -> str:
    acts = result["actions"]
    total_wait = sum(a["wait"] for a in acts)
    by: dict[str, list] = defaultdict(list)
    for a in acts:
        by[a["action"]].append(a)
    lines = [f"# Agent trajectory · {work}", "", f"engine {result['engine']}; {len(acts)} actions; model time before "
             f"actions {total_wait:.0f} s; end at {acts[-1]['end'] if acts else 0} s", "",
             "## By kind of action", "", "| action | n | model s before | own s | returned chars | failed | repeats |",
             "|---|---|---|---|---|---|---|"]
    for name, group in sorted(by.items(), key=lambda kv: -sum(a["wait"] for a in kv[1])):
        lines.append(f"| {name} | {len(group)} | {sum(a['wait'] for a in group):.0f} | "
                     f"{sum(a.get('seconds', 0) for a in group):.0f} | {sum(a.get('returned', 0) for a in group)} | "
                     f"{sum(1 for a in group if a.get('outcome', '').startswith(('failed', 'no call')) or '/' in a.get('outcome', ''))} | "
                     f"{sum(1 for a in group if a.get('repeat'))} |")
    lines += ["", "## Timeline", "", "| # | start | model s | action | what | own s | returned | outcome |",
              "|---|---|---|---|---|---|---|---|"]
    for n, a in enumerate(acts, 1):
        what = a.get("summary") or a.get("command", "")
        what = what.replace("|", "/").replace("\n", " ")[:90]
        outcome = (a.get("outcome") or "") + (" · repeat" if a.get("repeat") else "")
        lines.append(f"| {n} | {a['start']} | {a['wait']} | {a['action']} | {what} | {a.get('seconds', '')} | "
                     f"{a.get('returned', '')} | {outcome} |")
    problems = [(n, p) for n, a in enumerate(acts, 1) for p in a.get("problems", [])]
    if problems:
        lines += ["", "## Failures and refusals", ""] + [f"- #{n}: {p}" for n, p in problems]
    lines += ["", "## Review items", "", f"listed {result['items']['listed']}; closed by dismiss {result['items']['closed']}"]
    return "\n".join(lines) + "\n"


def summary(works: list[Path]) -> str:
    """Several runs side by side, by engine: time by kind of action, failures and refusals by cause, repeats,
    returned characters, the longest model steps."""
    rows, problems, long_steps = [], defaultdict(Counter), defaultdict(list)
    for work in works:
        result = analyse(work)
        acts = result["actions"]
        label = f"{result['engine']} · {work.parent.parent.name}/{work.parent.name}"
        by = defaultdict(lambda: [0, 0.0, 0.0, 0])
        for a in acts:
            kind = a["action"]
            by[kind][0] += 1
            by[kind][1] += a["wait"]
            by[kind][2] += a.get("seconds", 0) or 0
            by[kind][3] += a.get("returned", 0) or 0
            for p in a.get("problems", []):
                problems[result["engine"]][_cause(p)] += 1
            if a.get("outcome", "").startswith("no call"):
                problems[result["engine"]]["refused before the tool ran (bad request)"] += 1
            long_steps[result["engine"]].append((a["wait"], label, a["action"], (a.get("summary") or a.get("command", ""))[:70]))
        tools = sum(v[0] for k, v in by.items() if k.startswith("tool "))
        repeats = sum(1 for a in acts if a.get("repeat"))
        rows.append((label, acts[-1]["end"] if acts else 0, sum(a["wait"] for a in acts), len(acts), tools,
                     by.get("script", [0])[0], repeats, sum(v[3] for v in by.values()), dict(by)))
    lines = ["# Agent runs side by side", "", "| run | s | model s | actions | tool calls | scripts | repeats | "
             "returned chars |", "|---|---|---|---|---|---|---|---|"]
    for label, end, wait, n, tools, scripts, repeats, chars, _ in rows:
        lines.append(f"| {label} | {end:.0f} | {wait:.0f} | {n} | {tools} | {scripts} | {repeats} | {chars} |")
    lines += ["", "## Time by kind of action (all runs of an engine)", "",
              "| engine | action | n | model s before | own s | returned chars |", "|---|---|---|---|---|---|"]
    totals = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0.0, 0]))
    for label, *_, by in rows:
        for kind, v in by.items():
            t = totals[label.split(" · ")[0]][kind]
            for i in range(4):
                t[i] += v[i]
    for engine, kinds in totals.items():
        for kind, v in sorted(kinds.items(), key=lambda kv: -kv[1][1]):
            lines.append(f"| {engine} | {kind} | {v[0]} | {v[1]:.0f} | {v[2]:.0f} | {v[3]} |")
    lines += ["", "## Failures and refusals by cause", ""]
    for engine, causes in problems.items():
        lines.append(f"- {engine}: " + "; ".join(f"{c} ×{n}" for c, n in causes.most_common()))
    lines += ["", "## Longest model steps", ""]
    for engine, steps in long_steps.items():
        for wait, label, action, what in sorted(steps, reverse=True)[:12]:
            lines.append(f"- {engine} {wait:.0f} s before {action} ({what}) — {label}")
    return "\n".join(lines) + "\n"


def _cause(problem: str) -> str:
    """A failure or refusal reduced to its cause (the code and the rule, without ids and numbers)."""
    text = re.sub(r"[a-z]-[0-9a-f-]{6,}", "…", problem)
    text = re.sub(r"\d+", "N", text)
    return text[:90]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("work", type=Path, nargs="+")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    if len(args.work) > 1:
        text = summary(args.work)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return
    args.work = args.work[0]
    result = analyse(args.work)
    text = report(args.work, result)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    if args.json:
        for a in result["actions"]:
            a.pop("call", None)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
