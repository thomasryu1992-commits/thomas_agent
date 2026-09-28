# The deploy preflight runs from the clean tree, because the primary checkout may not have it

- **Why:** the first real use of the `deploy` skill failed at step 1. The step ran
  `python3 scripts/ops/deploy_preflight.py` in the primary checkout. That checkout was 42 commits
  behind origin/main and did not have the script yet. This is the same drift the preflight
  exists to warn about.
- **What changed:**
  - The skill and CLAUDE.md's Deploying block now create the clean origin/main worktree first.
  - Both preflight runs call `<tmp>/scripts/ops/deploy_preflight.py`.
  - The script itself is unchanged. It already reads git through `/root/thomas_agent` and docker
    through the host, so where it runs from does not change its result.
- **Checked:** `git show origin/main:scripts/ops/deploy_preflight.py | python3 - 1015` on the live
  host gave OK. The PR was merged, its tags were free, `latest` was the running image
  (candidate-1014), and it gave a WARN that the primary checkout was 42 commits behind.
