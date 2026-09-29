# The deploy preflight stops on a clean tree that is not origin/main

- **The defect:** `check_tree` reported a clean tree whose HEAD was not `origin/main` as WARN, and
  only STOP fails the run. So the run exited 0 for a stale, detached or branch tree. That
  contradicts the tool's own rule: build and compose only from a clean origin/main worktree.
  A clean tree at another commit ships something other than origin/main, which is exactly what
  the procedure exists to rule out. A tree that cannot resolve `origin/main` also fell through to
  the same WARN.
- **What changed:** both cases are now STOP (`scripts/ops/deploy_preflight.py`). Tests pin a clean
  tree off origin/main to STOP with exit 1 (the same host with the tree at origin/main exits 0),
  an unresolvable origin/main to STOP, and a clean tree exactly at origin/main to PASS. Mutation:
  turning either STOP back into WARN fails two tests.
- **Already pinned, unchanged:** dirty tree, bind source relative or inside the tree, a
  candidate or rollback tag another session holds, and the running image changing inside the
  promote window are all STOP.
