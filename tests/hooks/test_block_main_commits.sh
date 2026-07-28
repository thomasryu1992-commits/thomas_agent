#!/usr/bin/env bash
# The no-direct-main-commits hook, exercised against a real repo and a real worktree.
#
#   bash tests/hooks/test_block_main_commits.sh          # the repo's own hook
#   bash tests/hooks/test_block_main_commits.sh <path>   # a candidate
#
# Shell, not pytest, because the thing under test is a shell script reading a hook payload
# on stdin; driving it through Python would test a re-implementation of the harness.
#
# The two cases at the top are the ones that motivated it. The hook resolved the branch from
# its OWN working directory, which is not where the commit runs: with the primary tree on
# main every worktree commit was refused however correct its branch, and with the primary
# tree on a feature branch a `cd <worktree-on-main> && git commit` sailed through. One
# repository was being guarded and a different one committed to.
set -uo pipefail
HOOK=${1:-$(cd "$(dirname "$0")/../.." && pwd)/.claude/hooks/block-main-commits.sh}
BASE=$(mktemp -d)
trap 'rm -rf "$BASE"' EXIT

# A repo on main, and a worktree on a feature branch.
git init -q -b main "$BASE/repo"; cd "$BASE/repo"
git config user.email t@t; git config user.name t
echo x > a.txt; git add a.txt; git commit -qm init
git worktree add -q "$BASE/wt" -b feat/x
cd "$BASE/repo"

pass=0; fail=0
check() {  # name  expect(allow|deny)  cwd  command
  local name=$2 expect=$3 cwd=$4 cmd=$5
  local out
  out=$(cd "$cwd" && printf '{"tool_input":{"command":"%s"}}' "$cmd" | bash "$HOOK" 2>/dev/null)
  local got=allow
  [[ $out == *'"deny"'* ]] && got=deny
  if [[ $got == "$expect" ]]; then
    printf '  ok   %-52s %s\n' "$name" "$got"; pass=$((pass+1))
  else
    printf '  FAIL %-52s got=%s want=%s\n' "$name" "$got" "$expect"; fail=$((fail+1))
  fi
}

echo "== the two the old hook got wrong =="
check t "worktree on feature, cwd on main"        allow "$BASE/repo" "cd $BASE/wt \&\& git commit -m x"
check t "cwd on feature, command targets main"    deny  "$BASE/wt"   "cd $BASE/repo \&\& git commit -m x"

echo "== unchanged behaviour =="
check t "plain commit while on main"              deny  "$BASE/repo" "git commit -m x"
check t "plain commit while on feature"           allow "$BASE/wt"   "git commit -m x"
check t "git -C main"                             deny  "$BASE/wt"   "git -C $BASE/repo commit -m x"
check t "git -C worktree"                         allow "$BASE/repo" "git -C $BASE/wt commit -m x"
check t "non-commit git while on main"            allow "$BASE/repo" "git status"
check t "the word commit in a pipe"               allow "$BASE/repo" "git log | grep commit"
check t "echo then status"                        allow "$BASE/repo" "echo commit; git status"
check t "git -c k=v commit on main"               deny  "$BASE/repo" "git -c a.b=c commit -m x"

echo "== the message is prose, not instructions =="
# The first version of the fix scanned the whole payload and refused its own commit for
# quoting `cd <dir>` in the message describing the bug.
check t "message mentions cd, on a feature branch"    allow "$BASE/wt"  "git commit -m 'fixes cd /some/dir \&\& git commit'"
check t "message mentions a path, on main"           deny  "$BASE/repo" "git commit -m 'see cd $BASE/wt for context'"
check t "cd AFTER the commit does not count"         deny  "$BASE/repo" "git commit -m x \&\& cd $BASE/wt"

echo "== ambiguity =="
check t "two different targets"                   deny  "$BASE/repo" "cd $BASE/wt \&\& git -C $BASE/repo commit -m x"

echo "== not a repo =="
check t "outside any repo"                        allow "/tmp"       "git commit -m x"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
