#!/usr/bin/env bash
# PreToolUse(Bash) guard for the "No direct main commits" rule in CLAUDE.md.
#
# The rule was documentation-only, so it depended on Claude remembering it. It is
# easiest to break by accident, not by intent: an external process fast-forwards
# main mid-session and leaves HEAD on main, and the next `git commit` lands there.
#
# Detection is deliberately done on the raw hook payload rather than a parsed
# `.tool_input.command`: jq is not guaranteed to be installed, and a compound
# command (`cd x && git commit …`) must be caught too.
#
# The branch is then resolved for the directory the commit will ACTUALLY run in,
# which is not always this hook's own working directory. That distinction was
# missing and it broke both ways:
#
#   * False deny. This repo runs many worktrees (7+ concurrently), and after a
#     deploy the primary tree sits on main — its normal state. Every worktree
#     commit was then refused, however correct its branch, and the refusal named
#     a branch the commit was never going to touch. Not "one denied read-only
#     command" as the original comment assumed: all committing, blocked, until
#     someone moved the primary tree off main.
#   * False allow, the direction that actually matters. With the primary tree on
#     a feature branch, `cd <worktree-on-main> && git commit` sailed through —
#     the guard checked a repository the command was not using.
#
# Resolution order mirrors git's own: `git -C <dir>` wins (it applies to the git
# process itself), then a leading `cd <dir>`, then this hook's cwd. Genuine
# ambiguity — two different targets in one command — denies rather than guesses,
# because a guard that cannot tell which repository it is protecting is not one.
set -uo pipefail

payload=$(cat)

# The word `git`, then `commit`, with no shell separator in between — so
# `git -c k=v commit` and `git -C dir commit` are caught while `git log | grep commit`
# and `echo commit; git status` are not. A length bound keeps the gap inside one command.
if [[ ! $payload =~ (^|[^a-zA-Z0-9_])git[^\;\&\|]{0,120}[^a-zA-Z0-9_]commit([^a-zA-Z0-9_]|$) ]]; then
  exit 0
fi
invocation=${BASH_REMATCH[0]}

# --- where will git actually run? --------------------------------------------------
# Scanned over the text BEFORE the git invocation, plus the invocation itself — never the
# whole payload. The payload carries the commit message too, and a message that discusses
# `cd <dir>` or names a path is prose, not a target. The first version of this scanned
# everything and refused its own commit for quoting the bug it was fixing: a guard that
# reads documentation as instructions.
#
# A `cd` after the git call cannot affect it, so the prefix is the only place it counts,
# and `-C` belongs to the invocation by construction.
prefix=${payload%%"$invocation"*}
targets=()

# `git -C <dir>` — repeated flags are legal, so collect every one from the invocation.
scan=$invocation
while [[ $scan =~ (^|[^a-zA-Z0-9_])-C[[:space:]]+([^[:space:]\"\;\&\|]+) ]]; do
  targets+=("${BASH_REMATCH[2]}")
  scan=${scan/"${BASH_REMATCH[0]}"/}
done

# A `cd <dir>` ahead of it in the same command.
while [[ $prefix =~ (^|[^a-zA-Z0-9_])cd[[:space:]]+([^[:space:]\"\;\&\|]+) ]]; do
  targets+=("${BASH_REMATCH[2]}")
  prefix=${prefix/"${BASH_REMATCH[0]}"/}
done

deny() {
  # stdout JSON + exit 0 is the explicit deny channel; exit codes are reserved for
  # "the hook itself broke", which should look like a broken hook, not like a pass.
  cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"$1"}}
EOF
  exit 0
}

# Distinct targets in one command: which repo is being committed to is not knowable
# from the text, and picking one would be a guess in a guard.
uniq_targets=$(printf '%s\n' "${targets[@]:-}" | sort -u | grep -c . || true)
if [[ ${uniq_targets:-0} -gt 1 ]]; then
  deny "Refusing: this command names more than one directory ($(printf '%s ' "${targets[@]}")), so the guard cannot tell which repository the commit lands in. Split it into one commit per command."
fi

target=${targets[0]:-.}

# --- what branch is that? -----------------------------------------------------------
# `-C` on rev-parse so the check follows the target, and so a worktree reports its own
# HEAD rather than the primary tree's.
branch=$(git -C "$target" rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0

case "$branch" in
  main|master) ;;
  *) exit 0 ;;
esac

where=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null || echo "$target")
deny "Refusing to commit on '$branch' in $where. CLAUDE.md: no direct main commits — branch first (git checkout -b <type>/<name>), then PR. If HEAD was moved onto $branch by an external sync, re-create the feature branch before committing."
