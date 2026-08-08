#!/usr/bin/env bash
# PreToolUse(Bash) guard for the "No direct main commits" rule in CLAUDE.md.
#
# The rule was documentation-only, so it depended on Claude remembering it. It is
# easiest to break by accident, not by intent: an external process fast-forwards
# main mid-session and leaves HEAD on main, and the next `git commit` lands there.
#
# Detection is deliberately done on the raw hook payload rather than a parsed
# `.tool_input.command`: jq is not guaranteed to be installed. A false positive costs
# one denied read-only command while on main; a false negative costs a commit on main.
#
# This is the coarse net for the simple case (a bare `git commit` in the agent's own
# directory). The authority is .githooks/pre-commit — enable it once per clone with
# `git config core.hooksPath .githooks`; git then enforces the rule in every worktree,
# including commits this hook deliberately declines to judge.
#
# **This file is only reached if settings.json can find it, and for a while a miss was a
# silent pass.** The invocation used to be `bash "${CLAUDE_PROJECT_DIR:-.}/…"`, and that `.`
# is a guess, not a location: with the var unset and the agent's cwd one directory down,
# bash exits 127, which PreToolUse treats as a non-blocking error — the Bash call proceeds.
# Measured 2026-08-06 from `runtime/`: exit 127, command allowed. The wrapper in
# .claude/settings.json now resolves the repo root through `git rev-parse --show-toplevel`
# when the var is absent, and if the file is still unreadable it emits the deny JSON itself
# for any payload containing `git`…`commit`. That fallback glob is deliberately coarser than
# the regex below — it runs only when the real guard is gone, where over-denying is the
# cheap error. Nothing else about the contract moved: exit 0 is allow, stdout JSON is deny.
set -uo pipefail

payload=$(cat)

# The word `git`, then `commit`, with no shell separator in between — so
# `git -c k=v commit` and `git -C dir commit` are caught while `git log | grep commit`
# and `echo commit; git status` are not. A length bound keeps the gap inside one command.
if [[ ! $payload =~ (^|[^a-zA-Z0-9_])git[^\;\&\|]{0,120}[^a-zA-Z0-9_]commit([^a-zA-Z0-9_]|$) ]]; then
  exit 0
fi

# This hook runs in the AGENT's working directory, which is the primary checkout — not
# necessarily the tree the command commits to. A command that changes directory first
# (`cd <worktree> && git commit …`, `git -C <worktree> commit …`) lands somewhere this
# process cannot see, and judging it by the primary checkout's branch was wrong in the
# direction that hurts: with the primary checkout resting on main, every worktree commit
# was denied while the rule was already satisfied. Those cases are now left to
# .githooks/pre-commit, which git runs IN the committing worktree and which therefore
# knows the answer instead of inferring it.
#
# **The separator class has to admit an ESCAPED newline, and missing that reopened the
# hole for the commonest shape there is.** `payload` is the RAW JSON (line 18 — jq is not
# guaranteed here), so a newline inside the command arrives as the two characters `\` and
# `n`. In the ordinary multi-line form
#
#     SP=/tmp/…
#     cd "$SP/wt" && git add f && git commit …
#
# the character immediately before `cd` is therefore the letter **n**, which `[^a-zA-Z0-9_]`
# rejects — so the decline failed, the hook judged by the primary checkout's branch, and a
# legitimate worktree commit was denied the moment an external sync parked that checkout on
# main. Measured 2026-08-04 by replaying this regex: single-line `cd /tmp/wt && git commit`
# declined, `git -C …` declined, the multi-line form above JUDGED. It reads as intermittent
# because it only DENIES while the primary checkout happens to sit on main.
#
# `pushd` is admitted for the same reason `cd` is, and `env -C` for the same reason
# `git -C` is: all four move the commit somewhere this process cannot see.
if [[ $payload =~ (^|[^a-zA-Z0-9_]|\\n)(cd|pushd)[[:space:]] ]] \
  || [[ $payload =~ (git|env)[[:space:]]+-C[[:space:]] ]]; then
  exit 0
fi

branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || exit 0

case "$branch" in
  main|master) ;;
  *) exit 0 ;;
esac

# stdout JSON + exit 0 is the explicit deny channel; exit codes are reserved for
# "the hook itself broke", which should look like a broken hook, not like a pass.
cat <<EOF
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"Refusing to commit on '$branch'. CLAUDE.md: no direct main commits — branch first (git checkout -b <type>/<name>), then PR. If HEAD was moved onto $branch by an external sync, re-create the feature branch before committing."}}
EOF
