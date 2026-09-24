"""Codex runtime adapter (plan P2-1): command line, usage from the event stream, hygiene audit."""

import json
from pathlib import Path

from parserx.runtimes.codex import audit_events, exec_command, read_events, usage_from_events

HOME = Path("/Users/me")
REPO = HOME / "Projects" / "Repo"
EXP = HOME / "exp" / "phase2"
DOC = EXP / "r1" / "text_table01"


def _cmd(n, command, exit_code=0):
    return {"type": "item.completed", "item": {"id": f"item_{n}", "type": "command_execution", "command": command,
                                               "aggregated_output": "", "exit_code": exit_code,
                                               "status": "completed"}}


def _audit(*items):
    return audit_events(list(items), doc_dir=DOC, home=HOME, forbidden={"repository": REPO, "experiment root": EXP})


def test_command_line_names_model_effort_and_isolation():
    argv = exec_command(model="gpt-6-sol", effort="high", doc_dir=DOC, last_message=EXP / "_runs" / "m.md",
                        prompt="Read AGENTS.md")
    joined = " ".join(argv)
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "Read AGENTS.md"
    assert "-m gpt-6-sol" in joined and "model_reasoning_effort=high" in joined
    assert "--sandbox workspace-write" in joined and "sandbox_workspace_write.network_access=true" in joined
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json",
                 "--disable memories", "--disable plugins", "--disable multi_agent"):
        assert flag in joined
    assert f"-C {DOC}" in joined


def test_usage_is_read_from_the_event_stream(tmp_path):
    events = [
        {"type": "thread.started", "thread_id": "t-1"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "…"}},
        _cmd(1, "./px tool overview --ws ws --json"),
        _cmd(2, "./px tool read --ws ws --page 9 --json", exit_code=2),
        {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "done"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 50,
                                             "reasoning_output_tokens": 20}},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n")
    events, bad = read_events(path)
    usage = usage_from_events(events)
    assert bad == 1 and usage.thread_id == "t-1"
    assert (usage.turns, usage.commands, usage.failed_commands) == (2, 2, 1)
    assert usage.items == {"agent_message": 1, "command_execution": 2, "reasoning": 1}
    assert (usage.input_tokens, usage.cached_input_tokens, usage.output_tokens, usage.reasoning_output_tokens) == \
        (1010, 800, 55, 20)


def test_audit_accepts_work_inside_the_experiment_directory():
    result = _audit(
        _cmd(1, "/bin/zsh -lc './px tool read --ws ws --page 1 --image page --json'"),
        _cmd(2, f"/bin/zsh -lc 'cat {DOC}/ws/renders/p1.png | head -c 10; ls /tmp /private/var/folders/x'"),
        _cmd(3, "/bin/zsh -lc \"python3 - <<'EOF'\nfrom PIL import Image\nim = Image.open('/tmp/crop.png')\n"
                "print(im.size[0]/2, 'https://example.org/a/b')\nEOF\""),
        _cmd(4, "/bin/zsh -lc 'cd ws && ls renders && /usr/bin/env python3 -V'"),
        _cmd(6, "/bin/zsh -lc \"rg -n '<tr><td>(15|16)</td>' out/input.md; echo '<br/></th>'\""),
        {"type": "item.completed", "item": {"id": "item_5", "type": "file_change", "status": "completed",
                                            "changes": [{"path": str(DOC / "notes.py"), "kind": "add"}]}},
    )
    assert result.ok and result.hits == []


def test_audit_flags_answers_other_documents_and_codex_home():
    result = _audit(
        _cmd(1, f"/bin/zsh -lc 'cat {REPO}/ground_truth/text_table01/expected.md'"),
        _cmd(2, "/bin/zsh -lc 'cat ../ocr01/out/ocr01.md'"),
        _cmd(3, "/bin/zsh -lc 'ls ~/.codex/memories'"),
        _cmd(4, f"/bin/zsh -lc 'sed -n 1,5p {EXP}/r1/_toolkit/px-run'"),
        _cmd(5, "/bin/zsh -lc 'find / -name expected.md'"),
        _cmd(6, f"/bin/zsh -lc 'ls {HOME}/Downloads'"),
    )
    assert not result.ok
    by_item = {h.item: h.kind for h in result.hits}
    assert by_item == {"item_1": "forbidden", "item_2": "forbidden", "item_3": "forbidden", "item_4": "forbidden",
                       "item_5": "forbidden", "item_6": "outside"}


def test_audit_flags_tools_other_than_the_shell_and_writes_to_the_workspace():
    result = _audit(
        {"type": "item.completed", "item": {"id": "item_1", "type": "web_search", "query": "parserx expected"}},
        {"type": "item.completed", "item": {"id": "item_2", "type": "mcp_tool_call", "server": "x", "tool": "y"}},
        {"type": "item.completed", "item": {"id": "item_3", "type": "file_change", "status": "completed",
                                            "changes": [{"path": str(DOC / "ws" / "state.json"), "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "item_4", "type": "something_new"}},
    )
    assert not result.ok
    assert [(h.item, h.kind) for h in result.hits] == [("item_1", "tool_type"), ("item_2", "tool_type"),
                                                        ("item_3", "workspace_write")]
    assert [(n.item, n.kind) for n in result.notes] == [("item_4", "unknown_type")]


def test_timing_separates_model_steps_from_running_commands():
    from parserx.runtimes.codex import timing_from_events

    def started(n, cmd="./px tool read"):
        return {"type": "item.started", "item": {"id": f"item_{n}", "type": "command_execution", "command": cmd}}

    events = [
        ({"type": "turn.started"}, 0.0),
        (started(1), 4.0),                                    # model step 1: 4 s
        (_cmd(1, "./px tool read"), 5.0),                     # command 1 s
        (started(2), 8.0), (started(3), 8.1),                 # model step 2: 3 s, two commands in parallel
        (_cmd(2, "./px tool read"), 9.0), (_cmd(3, "./px tool read"), 10.0),
        ({"type": "item.completed", "item": {"id": "item_4", "type": "agent_message", "text": "done"}}, 16.0),
        ({"type": "turn.completed", "usage": {}}, 16.5),       # model step 3: 6.5 s
    ]
    timing = timing_from_events([e for e, _ in events], [t for _, t in events])
    assert timing.steps == 3 and timing.model_s == 13.5 and timing.command_s == 3.0
    assert timing.longest_step_s == 6.5 and timing.wall_s == 16.5
