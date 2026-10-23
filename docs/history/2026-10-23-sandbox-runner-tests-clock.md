# Sandbox runner tests pin their clock outside the factory window

- **Why:** #1284 made `sandbox_run.sh` refuse to start between 07:30 and 09:00 UTC. The runner tests in
  `tests/test_research_sandbox.py` start the real runner, here and in CI. On 2026-10-23 at 07:38 a local run failed
  `test_a_run_sees_the_snapshot_records_itself_and_leaves_no_link` with the runner's own refusal, and at 07:42 the CI
  run of #1291 failed the same three tests. The suite passed or failed depending on the time of day.
  - **Correction (2026-10-23, after the merge):** this note first said CI skips these tests for lack of user systemd.
    It does not; #1291's CI log shows them running and failing on the clock.
- **What:** the `sandbox` fixture sets `RESEARCH_NOW_UTC=12:00`, the clock hook #1284 added for its own test. The
  window rule keeps its dedicated test, which drives the clock explicitly.
- **Checked:** the file passes at 07:39 UTC, inside the window, with nothing skipped.
