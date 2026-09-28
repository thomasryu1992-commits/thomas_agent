# A local test runner that handles this host's traps instead of relying on memory of them

- **Why:** the same local test run was misread in six recorded ways on this host. A full `/tmp`
  tmpfs or low memory turned into "regressions" (2026-08-23: 37 of them). A worktree without a
  Core activation skipped ~208 tests and still read green. A long `--basetemp` broke the AF_UNIX
  tests. Basetemps left behind filled `/tmp` (2026-09-25). A same-length edit reused a stale
  `.pyc`, so the edit never ran. An edit or commit during the suite faked failures. Each trap was
  written down after it happened, and each depended on the next session remembering it.
- **What changed:** `scripts/ops/test_run.sh [--gate] [pytest args…]`.
  - It refuses to start below a `/tmp` and memory floor, and prints both numbers.
  - It activates a Core when the pointer is missing. It never adds `--allow-foreign-root-run`:
    that refusal protects a live state directory.
  - One short `mktemp` directory holds the basetemp, the junit report and a
    `PYTHONPYCACHEPREFIX`. It is removed on exit by its exact path.
  - It fingerprints the tree (HEAD, status, diff) before and after, and fails the run if either
    changed.
  - It runs `scripts/check_test_skips.py`. A root run is held to the two documented chown skips
    rather than CI's 0.
  - `--gate` adds the release gate, which runs no pytest.
  - The last line is always `TEST_RUN_EXIT=<n>`, so a waiter can grep a log instead of
    `pgrep -f`, which matches its own command line.
- **Tests:** `tests/test_ops_test_run.py`, 7 cases, run against a scratch git repository with a
  stub interpreter. Four hand mutations were each caught: ignoring the fingerprint, keeping the
  work directory, dropping the root ceiling, and ignoring a refused activation. The cases are
  POSIX-only, so the win32 skip ceiling rises from 87 to 94.
- **Deliberately not done:** the runner does not replace `pytest` in CI or change what CI runs.
  It does not clean anything it did not create. It does not retry an activation the state guard
  refused.
