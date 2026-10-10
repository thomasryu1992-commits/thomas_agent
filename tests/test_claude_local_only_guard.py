"""The LOCAL_ONLY guard (`.claude/hooks/guard-local-only.sh`) and its wiring in `.claude/settings.json`.

Every payload here is synthetic: the paths name files that do not exist in the scratch directory the
hook runs in, and the hook reads command text only, so nothing here opens a holdings file. What is
pinned is the guard's two directions. It refuses the ways into the lane's local-only state seen so far,
and it leaves the ordinary work alone: the one table, other state files, and the lane's source and
tests. It is a tripwire, not a boundary (see the hook's header). These tests check that it trips. They
do not show that it cannot be bypassed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "guard-local-only.sh"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the guard is a bash script")


def _payload(tool: str, **tool_input: str) -> str:
    return json.dumps({"tool_name": tool, "tool_input": tool_input})


def _run(command: list[str], stdin: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(command, input=stdin, capture_output=True, text=True, cwd=cwd, env=env, timeout=30)
    assert done.returncode == 0, done.stderr  # a non-zero exit is a broken hook, which Claude Code lets pass
    return done.stdout


def _decision(stdin: str, cwd: Path) -> str:
    out = _run(["bash", str(HOOK)], stdin, cwd)
    if not out.strip():
        return "allow"
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


DENIED = [
    ("Bash", "cat .runtime_governance_state/holdings/holdings_local.json"),
    ("Bash", "python3 -c \"import json; json.load(open('/root/thomas_agent/.runtime_governance_state/holdings/x.json'))\""),
    ("Bash", "docker exec thomas-scheduler cat /app/.runtime_governance_state/holdings/holdings_snapshot.json"),
    ("Bash", "docker cp thomas-scheduler:/app/.runtime_governance_state/holdings ./h"),
    ("Bash", "cat /root/wt-other/.runtime_governance_state/holdings/a.json"),
    ("Bash", "cd .runtime_governance_state && cat holdings/holdings_baselines.jsonl"),
    ("Bash", "jq . holdings_local.json"),
    ("Bash", "head holdings_cash_flow_state.json"),
    ("Bash", "wc -l holdings_cash_flows.jsonl"),
    ("Bash", "cat holdings_classification.json"),
    ("Bash", "cat .runtime_governance_state/*/holdings_x"),
    ("Bash", "ls .runtime_governance_state/h*"),
    ("Bash", "grep -r usd .runtime_governance_state"),
    ("Bash", "grep -Rn usd .runtime_governance_state/"),
    ("Bash", "rg usd .runtime_governance_state"),
    ("Bash", "find .runtime_governance_state -name '*.json'"),
    ("Bash", "tar czf /tmp/s.tgz .runtime_governance_state"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --local"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --full"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --unclassified"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --classify binance:X=coin"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --reset-peak --reason r"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --cash-flow-cutover 2026-10-09T00:00:00Z"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --flows"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --resolve 3 deposit"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --semantics-epoch --reason r"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board \\\n  --local"),
    ("Bash", "python3 - <<'EOF'\nfrom runtime.mvp_runtime.holdings import classification\nEOF"),
    ("Bash", "docker exec thomas-scheduler python -c 'from runtime.mvp_runtime import holdings'"),
]

ALLOWED = [
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board"),
    ("Bash", "docker exec thomas-scheduler python -m scripts.holdings_board --json"),
    ("Bash", "cat .runtime_governance_state/schedules.jsonl"),
    ("Bash", "ls .runtime_governance_state/crypto"),
    ("Bash", "ls .runtime_governance_state/ && du -sh .runtime_governance_state"),
    ("Bash", ".venv/bin/python -m pytest tests/test_mvp_runtime_holdings_cash_flows.py "
             "tests/test_mvp_runtime_holdings_classification.py -q"),
    ("Bash", "git show origin/main:runtime/mvp_runtime/holdings/cash_flows.py"),
    ("Bash", "git grep -n mvp_runtime.holdings -- tests"),
    ("Bash", "scripts/ops/harness_backup.sh core"),
    ("Bash", "git status"),
]


@posix_only
@pytest.mark.parametrize(("tool", "command"), DENIED)
def test_the_ways_into_local_only_state_are_refused(tmp_path, tool, command):
    assert _decision(_payload(tool, command=command), tmp_path) == "deny"


@posix_only
@pytest.mark.parametrize(("tool", "command"), ALLOWED)
def test_the_one_table_and_ordinary_work_pass(tmp_path, tool, command):
    assert _decision(_payload(tool, command=command), tmp_path) == "allow"


@posix_only
@pytest.mark.parametrize(("tool", "tool_input", "expected"), [
    ("Read", {"file_path": "/root/thomas_agent/.runtime_governance_state/holdings/holdings_local.json"}, "deny"),
    ("Grep", {"pattern": "usd", "path": "/root/thomas_agent/.runtime_governance_state/holdings"}, "deny"),
    ("Glob", {"pattern": ".runtime_governance_state/holdings/*"}, "deny"),
    ("Glob", {"pattern": ".runtime_governance_state/*"}, "deny"),
    ("Read", {"file_path": "/root/thomas_agent/runtime/mvp_runtime/holdings/classification.py"}, "allow"),
    ("Read", {"file_path": "/root/thomas_agent/.runtime_governance_state/schedules.jsonl"}, "allow"),
    # The Bash-only rules do not fire on a file tool's input that merely mentions a flag.
    ("Grep", {"pattern": "--local", "path": "scripts/holdings_board.py"}, "allow"),
])
def test_the_file_tools_are_judged_by_their_paths(tmp_path, tool, tool_input, expected):
    assert _decision(_payload(tool, **tool_input), tmp_path) == expected


@posix_only
def test_the_command_is_judged_not_the_description(tmp_path):
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": "ls", "description": "skip holdings_local.json"}})
    assert _decision(payload, tmp_path) == "allow"


@posix_only
def test_a_payload_that_does_not_parse_is_judged_raw(tmp_path):
    assert _decision("not json .runtime_governance_state/holdings/x", tmp_path) == "deny"
    assert _decision("not json, nothing local", tmp_path) == "allow"


def _wrapper() -> str:
    entries = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    commands = [hook["command"] for entry in entries for hook in entry["hooks"]
                if "guard-local-only.sh" in hook["command"]]
    assert len(commands) == 1
    return commands[0]


def test_the_guard_is_wired_for_every_tool_that_reads():
    entries = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    matchers = [entry["matcher"] for entry in entries
                if any("guard-local-only.sh" in hook["command"] for hook in entry["hooks"])]
    assert matchers and set(matchers[0].split("|")) >= {"Bash", "Read", "Grep", "Glob", "Monitor"}
    # The main-commit guard keeps its own entry, unchanged in kind.
    assert any(entry["matcher"] == "Bash" and "block-main-commits.sh" in entry["hooks"][0]["command"]
               for entry in entries)


def test_the_read_deny_rules_cover_both_anchors():
    deny = json.loads(SETTINGS.read_text(encoding="utf-8"))["permissions"]["deny"]
    for anchor in ("/.runtime_governance_state/holdings", "//root/thomas_agent/.runtime_governance_state/holdings"):
        assert f"Read({anchor})" in deny
        assert f"Read({anchor}/**)" in deny


@posix_only
@pytest.mark.parametrize(("command", "expected"), [
    ("cat .runtime_governance_state/holdings/a.json", "deny"),
    ("docker exec thomas-scheduler python -m scripts.holdings_board", "allow"),
])
def test_the_settings_wrapper_runs_the_guard(tmp_path, command, expected):
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    out = _run(["bash", "-c", _wrapper()], _payload("Bash", command=command), tmp_path, env)
    assert ("deny" if '"deny"' in out else "allow") == expected


@posix_only
@pytest.mark.parametrize(("command", "expected"), [
    ("cat .runtime_governance_state/holdings/a.json", "deny"),
    ("docker exec thomas-scheduler python -m scripts.holdings_board --local", "deny"),
    ("git status", "allow"),
])
def test_without_the_guard_the_wrapper_still_refuses_a_holdings_reference(tmp_path, command, expected):
    """A missing guard must not be a silent pass (the lesson block-main-commits.sh records)."""
    env = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    out = _run(["bash", "-c", _wrapper()], _payload("Bash", command=command), tmp_path, env)
    assert ("deny" if '"deny"' in out else "allow") == expected


# --- Monitor runs a shell command too (2026-10-09) ----------------------------------------------------
# Its input is {"command": ..., "description": ..., "timeout_ms": ...} or {"ws": {...}}. Before this, a
# Monitor command was judged only as JSON text for paths: the terminal-only modes and the holdings import
# went through. Now a Monitor `command` is judged exactly as Bash's.

TERMINAL_ONLY = ("--full", "--local", "--unclassified", "--classify binance:X=coin", "--unclassify binance:X",
                 "--reset-peak --reason r", "--cash-flow-cutover 2026-10-09T00:00:00Z", "--flows",
                 "--resolve pay:x --kind deposit", "--semantics-epoch --change-ref '#1'")


def _monitor(**tool_input) -> str:
    return json.dumps({"tool_name": "Monitor", "tool_input": {"description": "synthetic", "timeout_ms": 1000,
                                                                **tool_input}})


@posix_only
@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("flag", TERMINAL_ONLY)
def test_a_terminal_only_mode_is_refused_from_bash_and_monitor(tmp_path, tool, flag):
    command = f"docker exec -it thomas-scheduler-maint python -m scripts.holdings_board {flag}"
    payload = _payload("Bash", command=command) if tool == "Bash" else _monitor(command=command)
    assert _decision(payload, tmp_path) == "deny"


@posix_only
@pytest.mark.parametrize(("tool_input", "expected"), [
    ({"command": "python3 -c 'from runtime.mvp_runtime.holdings import store'"}, "deny"),
    ({"command": "python -c 'from runtime.mvp_runtime import holdings'"}, "deny"),
    ({"command": "tail -f /root/thomas_agent/.runtime_governance_state/holdings/holdings_cash_flows.jsonl"}, "deny"),
    ({"command": "grep -r usd /root/thomas_agent/.runtime_governance_state"}, "deny"),
    ({"ws": {"url": "wss://example.invalid/.runtime_governance_state/holdings"}}, "deny"),
    ({}, "deny"),                                                     # neither a command nor a socket
    ({"command": ["tail", "-f", "x"]}, "deny"),                       # a command that is not a string
    ({"command": "docker exec thomas-scheduler python -m scripts.holdings_board"}, "allow"),
    ({"command": "docker exec thomas-scheduler python -m scripts.holdings_board --json"}, "allow"),
    ({"command": "tail -f /var/log/app.log | grep --line-buffered ERROR"}, "allow"),
    ({"command": "until gh pr checks 1 --json bucket | grep -q pending; do sleep 30; done"}, "allow"),
    ({"command": "tail -f run.log", "description": "watch holdings_local.json for a test"}, "allow"),
    ({"ws": {"url": "wss://events.example.invalid/stream"}}, "allow"),
])
def test_monitor_is_judged_like_bash(tmp_path, tool_input, expected):
    assert _decision(_monitor(**tool_input), tmp_path) == expected


@posix_only
@pytest.mark.parametrize("command", [
    "python3 -c 'from runtime.mvp_runtime.holdings import store'",
    "docker exec thomas-scheduler python -m scripts.holdings_board --flows",
    "docker exec thomas-scheduler python -m scripts.holdings_board --json",
    "tail -f /var/log/app.log",
])
def test_bash_and_monitor_give_the_same_answer(tmp_path, command):
    assert _decision(_payload("Bash", command=command), tmp_path) == _decision(_monitor(command=command), tmp_path)


@posix_only
@pytest.mark.parametrize(("payload", "expected"), [
    (_monitor(command="docker exec thomas-scheduler python -m scripts.holdings_board --full"), "deny"),
    (_monitor(command="tail -f .runtime_governance_state/holdings/a.json"), "deny"),
    (_monitor(command="tail -f /var/log/app.log"), "allow"),
])
def test_the_settings_wrapper_judges_monitor_with_and_without_the_guard(tmp_path, payload, expected):
    with_guard = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    without = {"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    for env in (with_guard, without):
        out = _run(["bash", "-c", _wrapper()], payload, tmp_path, env)
        assert ("deny" if '"deny"' in out else "allow") == expected
