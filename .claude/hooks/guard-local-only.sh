#!/usr/bin/env bash
# PreToolUse(Bash|Read|Grep|Glob) guard for the holdings lane's LOCAL_ONLY files.
#
# The holdings lane keeps two kinds of output (H3, `runtime/mvp_runtime/holdings/disclosure.py`): one
# table that may leave the process (`holdings_snapshot.json`, rendered by `holdings_board` and `--json`),
# and everything else, which stays on this host: the per-wallet and sub-class breakdowns, the Toss
# cash, the classification entries that name holdings, the cash-flow ledger and its state, and the
# baseline log. A model prompt is one of the ways out that H3 names. On 2026-10-09 a session read the
# classification file and the local board directly, and derived one instrument's share from the
# breakdowns. Nothing stopped it: Claude runs as root here, and the state directory's 0700 owner does
# not bind root.
#
# **This is a tripwire, not a boundary.** It reads the command TEXT and opens no file. A command that
# assembles the path at run time, or a script written to disk and then run, passes it. So does any
# user in the `docker` group, root or not (`docker exec … cat`). The boundary that would hold is OS
# separation: a non-root user outside the docker group, with sudo wrappers for the few things Claude
# must do. That is a separate decision; see docs/history/2026-10-09-local-only-guard.md.
#
# What it refuses:
#   - any path into the holdings state directory, whatever comes before it (`/app/…` in a container,
#     `/root/thomas_agent/…`, a worktree, `docker cp`);
#   - a state file's name, or `holdings/holdings_…`, for a command run from inside the state root;
#   - a glob in the first path segment under the state root, which could expand into holdings;
#   - for a command that runs (Bash's or Monitor's `command`): a recursive read of the whole state root,
#     the terminal-only `holdings_board` modes, and inline Python that imports the holdings package;
#   - a Bash or Monitor input with no command it can read.
# What stays allowed: `holdings_board` and `holdings_board --json` (the one table), other state files
# (`schedules.jsonl`, `crypto/…`), `ls`/`du` of the state root, and the source and tests of the lane.
#
# Contract (same as block-main-commits.sh): exit 0 with no output is allow; stdout JSON is deny.
set -uo pipefail

payload=$(cat)

# Judge the command for the tools that run one, and the tool input for the file tools, not the whole
# payload, so that a `description` naming a file does not trip it. Bash and Monitor both run a shell
# `command` (Monitor's other form, `ws`, opens a WebSocket and runs no shell, so it gets the path checks
# only). A command tool whose input has neither is not a shape this guard knows: it is refused. python3
# parses it (jq is not guaranteed). If parsing fails, the raw payload is judged as a command: denying too
# much is the cheap error here.
parsed=$(printf '%s' "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)
tool = d.get("tool_name") or ""
ti = d.get("tool_input") or {}
print(tool)
if tool in ("Bash", "Monitor") and isinstance(ti.get("command"), str):
    print("command"); print(ti["command"])
elif tool == "Monitor" and isinstance(ti.get("ws"), dict) and "command" not in ti:
    print("socket"); print(json.dumps(ti))
elif tool in ("Bash", "Monitor"):
    print("unknown"); print(json.dumps(ti))
else:
    print("file"); print(json.dumps(ti))
' 2>/dev/null) || parsed=$'\n'"raw"$'\n'"$payload"
tool=${parsed%%$'\n'*}
rest=${parsed#*$'\n'}
kind=${rest%%$'\n'*}
subject=""
[[ $rest == *$'\n'* ]] && subject=${rest#*$'\n'}
# One line, so a flag after a line continuation still sits next to its command.
flat=$(printf '%s' "$subject" | tr '\n\\' '  ')

deny() {
  local why=$1
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' \
    "LOCAL_ONLY holdings data ($why). Use the one table instead: docker exec thomas-scheduler python -m scripts.holdings_board [--json]. Anything else is for Thomas to run in his own terminal and summarise; see CLAUDE.md, the LOCAL_ONLY guardrail."
  exit 0
}

state='runtime_governance_state/+'
[[ $flat =~ ${state}holdings ]] && deny "a path into the holdings state directory"
[[ $flat =~ holdings/+holdings_ ]] && deny "a holdings state file"
[[ $flat =~ holdings_(classification|local)\.json|holdings_cash_flows?(_state)?\.jsonl?|holdings_baselines\.jsonl ]] \
  && deny "a holdings state file by name"
[[ $flat =~ ${state}[^/[:space:]\"\']*[*?[{] ]] && deny "a glob under the state root"
[[ $kind == unknown ]] && deny "a $tool input without a command this guard can read"

# The rules for a command that runs: Bash and Monitor alike, and a raw payload that did not parse.
if [[ $kind == command || $kind == raw ]]; then
  root_ref="${state}?([[:space:]\"\']|$)"
  recursive='(grep|egrep)[^;&|]*[[:space:]]-[a-zA-Z]*[rR]|(^|[^a-zA-Z0-9_])(rg|ag|ack|find|tar|zip|7z|rsync)[[:space:]]|(^|[^a-zA-Z0-9_])(cp|scp)[^;&|]*[[:space:]]-[a-zA-Z]*[rRa]'
  if [[ $flat =~ $root_ref ]] && [[ $flat =~ $recursive ]]; then
    deny "a recursive read of the state root"
  fi
  [[ $flat =~ holdings_board[^\;\&\|]*--(full|local|unclassified|classify|unclassify|reset-peak|cash-flow-cutover|migrate|shadow-started|flows|resolve|semantics-epoch)([^a-z-]|$) ]] \
    && deny "a terminal-only holdings_board mode"
  if [[ $flat =~ (^|[^a-zA-Z0-9_])python[0-9.]*([[:space:]]|$) ]] \
    && [[ $flat =~ mvp_runtime\.holdings|mvp_runtime[[:space:]]+import[[:space:]]+holdings ]]; then
    deny "inline Python importing the holdings package"
  fi
fi
exit 0
