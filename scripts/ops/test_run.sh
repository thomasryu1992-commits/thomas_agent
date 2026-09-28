#!/bin/bash
# Local test run with this host's traps handled — the runner, not a checklist to remember.
#
# Every step below exists because a session on this host once read a green or red run wrongly:
#   - /tmp is a 1.9 GB tmpfs and the host has ~3.8 GB of RAM shared with a dozen sessions. When
#     either runs out, tests FAIL rather than saying so (2026-08-23: 37 "regressions" that were a
#     full /tmp). The run refuses to start below the floors and prints the numbers.
#   - A worktree without a local Core activation skips ~208 tests and still ends green. The run
#     activates one when the pointer is missing (never with --allow-foreign-root-run: that refusal
#     is the live state directory protecting itself) and then runs scripts/check_test_skips.py.
#   - A long --basetemp breaks the AF_UNIX tests (108-byte socket path limit); pytest-of-root and
#     per-run basetemps left in /tmp filled it (2026-09-25). Everything this run writes lives in one
#     short mktemp directory, removed on exit by its exact path.
#   - A same-length edit written within the same second reuses a stale .pyc, so the edit never
#     runs. PYTHONPYCACHEPREFIX points into that directory, so every run compiles fresh.
#   - Editing or committing in the tree while the suite runs fakes failures (diagnostic-index line
#     numbers, the activation test's HEAD check). The tree is fingerprinted before and after, and a
#     change fails the run: its result describes neither version.
#   - Waiting on a run with `pgrep -f <pattern>` matches the waiting shell's own command line and
#     never ends. The last line is always `TEST_RUN_EXIT=<n>`: wait on that in a log instead.
#
# Usage:
#   scripts/ops/test_run.sh [--gate] [pytest args…]     # no pytest args = tests/
#     --gate   also run the release gate afterwards. It runs no pytest and pytest runs no gate
#              validator, so a PR needs both.
#
# Environment:
#   THOMAS_TEST_PY          interpreter (default /root/thomas_agent/.venv/bin/python — a new
#                           worktree has no .venv, and a symlinked one is not gitignored)
#   TEST_RUN_MIN_TMP_MB     free space /tmp must have to start (default 400)
#   TEST_RUN_MIN_MEM_MB     MemAvailable needed to start (default 500)
#   TEST_RUN_FORCE=1        start below those floors anyway (the numbers are still printed)
#
# Exit: pytest's own code when it failed; otherwise 0, or 3 resources below the floor, 4 Core
# activation refused or failed, 5 the tree changed during the run, 6 the skip check failed,
# 7 the release gate failed.
set -u

PY="${THOMAS_TEST_PY:-/root/thomas_agent/.venv/bin/python}"
MIN_TMP_MB="${TEST_RUN_MIN_TMP_MB:-400}"
MIN_MEM_MB="${TEST_RUN_MIN_MEM_MB:-500}"

finish() {
    echo "TEST_RUN_EXIT=$1"
    exit "$1"
}

ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null) || {
    echo "test_run: not inside a git checkout" >&2
    finish 2
}
cd "$ROOT" || finish 2

GATE=0
if [ "${1:-}" = "--gate" ]; then
    GATE=1
    shift
fi
if [ "$#" -eq 0 ]; then
    # A bare `pytest` also collects scripts/test_apply_core_idempotency.py and stops at collection.
    set -- tests/
fi

if [ ! -x "$PY" ]; then
    echo "test_run: interpreter not found: $PY (set THOMAS_TEST_PY)" >&2
    finish 2
fi

# --- resources ----------------------------------------------------------------------------------
tmp_mb=$(df -Pm /tmp | awk 'NR==2 {print $4}')
mem_mb=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo)
echo "test_run: /tmp free ${tmp_mb} MB (floor ${MIN_TMP_MB}), memory available ${mem_mb} MB (floor ${MIN_MEM_MB})"
if [ "$tmp_mb" -lt "$MIN_TMP_MB" ] || [ "$mem_mb" -lt "$MIN_MEM_MB" ]; then
    if [ "${TEST_RUN_FORCE:-0}" != "1" ]; then
        echo "test_run: below the floor — failures now would read as regressions. Free space first" >&2
        echo "          (your own basetemps, finished sessions' /tmp/claude-0 dirs), or TEST_RUN_FORCE=1." >&2
        finish 3
    fi
    echo "test_run: below the floor, continuing because TEST_RUN_FORCE=1 — treat mass failures as resource failures"
fi

# --- one short work directory, removed by its exact path -----------------------------------------
WORK=$(mktemp -d /tmp/tr-XXXXXX) || finish 2
trap 'rm -rf -- "$WORK"' EXIT
export PYTHONPYCACHEPREFIX="$WORK/pyc"

# --- Core activation ----------------------------------------------------------------------------
if [ ! -f "$ROOT/.runtime_governance_state/CURRENT_CORE_RELEASE.yaml" ]; then
    echo "test_run: no local Core activation here — activating (scripts/ci_activate_core_for_tests.py)"
    if ! "$PY" scripts/ci_activate_core_for_tests.py; then
        echo "test_run: Core activation refused or failed (above). Not retrying with an override:" >&2
        echo "          run the suite in a worktree, not against a live state directory." >&2
        finish 4
    fi
fi

fingerprint() {
    {
        git rev-parse HEAD
        git status --porcelain=v1 -uall
        git diff HEAD
    } 2>/dev/null | sha256sum | cut -d' ' -f1
}
before=$(fingerprint)

# --- pytest -------------------------------------------------------------------------------------
"$PY" -m pytest -q "--basetemp=$WORK/b" "--junitxml=$WORK/junit.xml" "$@"
rc=$?

after=$(fingerprint)
if [ "$before" != "$after" ]; then
    echo "test_run: the tree changed while the suite ran (edit, commit or checkout) — this result" >&2
    echo "          describes neither version. Re-run without touching the tree." >&2
    finish 5
fi
if [ "$rc" -ne 0 ]; then
    finish "$rc"
fi

# --- skipped tests ------------------------------------------------------------------------------
skip_args=(--junit "$WORK/junit.xml")
if [ "$(id -u)" = "0" ] && [ "$(uname -s)" = "Linux" ]; then
    # tests/skip_ceiling.json records CI's linux ceiling of 0. A root run also skips the two
    # chown-refusal tests (root can chown to any gid), as that file says, so a root run is held to
    # 2 — a third skip still fails, and "no local Core activation" fails at any count.
    printf '{"ceilings": {"linux": 2}}\n' > "$WORK/ceiling.json"
    skip_args+=(--ceilings "$WORK/ceiling.json")
fi
if ! "$PY" scripts/check_test_skips.py "${skip_args[@]}"; then
    finish 6
fi

# --- release gate -------------------------------------------------------------------------------
if [ "$GATE" = "1" ]; then
    if ! "$PY" scripts/run_repository_release_gate.py --full --check-only; then
        finish 7
    fi
fi

finish 0
