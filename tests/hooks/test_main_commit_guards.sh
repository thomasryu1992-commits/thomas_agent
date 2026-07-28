#!/usr/bin/env bash
# The two "no direct main commits" guards, exercised against a real repo and a real worktree.
#
#   bash tests/hooks/test_main_commit_guards.sh
#
# Shell rather than pytest: the subjects are shell scripts — one reading a hook payload on
# stdin, one run by git with a working directory as its whole input. Driving them through
# Python would test a re-implementation of the harness rather than the harness.
#
# The two are deliberately different in kind, and the tests are split the same way:
#
#   .githooks/pre-commit          the authority. Git runs it IN the worktree being committed
#                                 to, so its `rev-parse` needs no inference and cannot be
#                                 fooled by how the command was written.
#   .claude/hooks/block-main-commits.sh
#                                 the coarse net, for a bare `git commit` in the agent's own
#                                 directory. It runs in the agent's cwd — NOT necessarily the
#                                 tree being committed to — so it declines to judge any
#                                 command that changes directory first, and leaves those to
#                                 the git hook.
#
# What is pinned here is that division of labour. The interesting failures are a guard that
# judges a tree it cannot see (the coarse net answering for a worktree) and a guard that
# stops covering the case it exists for (the git hook passing on main).
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PRE_COMMIT="$ROOT/.githooks/pre-commit"
COARSE="$ROOT/.claude/hooks/block-main-commits.sh"

BASE=$(mktemp -d)
trap 'rm -rf "$BASE"' EXIT

git init -q -b main "$BASE/repo"
cd "$BASE/repo"
git config user.email t@t
git config user.name t
echo x > a.txt
git add a.txt
git -c core.hooksPath=/dev/null commit -qm init
git worktree add -q "$BASE/wt" -b feat/x
git worktree add -q --detach "$BASE/det" HEAD

pass=0
fail=0
check() {  # name  expect  actual
  if [[ $2 == "$3" ]]; then
    printf '  ok   %-54s %s\n' "$1" "$3"; pass=$((pass+1))
  else
    printf '  FAIL %-54s got=%s want=%s\n' "$1" "$3" "$2"; fail=$((fail+1))
  fi
}

# --- the authority: git runs it where the commit lands ------------------------------
git_hook() {  # <dir> -> refuse|allow
  ( cd "$1" && bash "$PRE_COMMIT" >/dev/null 2>&1 ) && echo allow || echo refuse
}

echo "== .githooks/pre-commit — judged by the worktree it runs in =="
check "primary checkout on main"        refuse "$(git_hook "$BASE/repo")"
check "worktree on a feature branch"    allow  "$(git_hook "$BASE/wt")"
# A rebase or a bisect must stay usable, and a detached HEAD is not main.
check "detached HEAD"                   allow  "$(git_hook "$BASE/det")"

# --- the coarse net: judged by the agent's cwd, and it knows that -------------------
coarse() {  # <cwd> <command> -> deny|allow
  local out
  out=$(cd "$1" && printf '{"tool_input":{"command":"%s"}}' "$2" | bash "$COARSE" 2>/dev/null)
  [[ $out == *'"deny"'* ]] && echo deny || echo allow
}

echo "== .claude/hooks/block-main-commits.sh — the simple case only =="
check "bare commit, cwd on main"        deny   "$(coarse "$BASE/repo" "git commit -m x")"
check "bare commit, cwd on a feature"   allow  "$(coarse "$BASE/wt"   "git commit -m x")"

echo "== ...and it declines what it cannot see =="
# Judging these by the agent's cwd is what denied every worktree commit while the primary
# checkout rested on main. Deferring is the fix, and the git hook is what still covers them.
check "cd elsewhere then commit"        allow  "$(coarse "$BASE/repo" "cd $BASE/wt \&\& git commit -m x")"
check "git -C elsewhere"                allow  "$(coarse "$BASE/repo" "git -C $BASE/wt commit -m x")"

echo "== not a commit at all =="
check "git status on main"              allow  "$(coarse "$BASE/repo" "git status")"
check "the word commit in a pipe"       allow  "$(coarse "$BASE/repo" "git log | grep commit")"
check "echo commit then status"         allow  "$(coarse "$BASE/repo" "echo commit; git status")"
check "git -c k=v commit on main"       deny   "$(coarse "$BASE/repo" "git -c a.b=c commit -m x")"

echo "== outside any repository =="
check "no repo to judge"                allow  "$(coarse /tmp "git commit -m x")"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
