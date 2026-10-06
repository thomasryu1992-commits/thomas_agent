# The forward reports index their rows once; each lineage reads only its own

- **Why:** THROUGHPUT P1-2 (`docs/proposals/RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md`, decided
  2026-10-06 via scorecard Q3). `cohort_report` and `null_report` handed every member every row, and
  `forward_outcomes_for` walked all of them to keep its own: O(members × rows), before the cohort grows
  toward 500.
- **What changed:** `forward_confirmation.index_outcomes` groups the closed rows by attribution key once,
  with their store position; `rows_for` gives a lineage its own rows back in store order (a lineage with
  both a `cand:` and a `gen:` key gets them merged by position). Both reports pass that narrowed list to
  the same `priced_nets` / `judge_forward`. The cutoff, pricing and every judging rule are untouched, and
  the LIVE door's single-record call is unchanged.
- **Same answers, checked two ways:** a test reports one store both ways (indexed, and with `rows_for`
  patched back to "every row") with a pre-selection row, an open row, a `gen:`-attributed row and a
  non-row in it, and asserts equality; a mutation that drops `gen:` keys fails it. On a copy of the
  host's cohort files (197 members, 1,077 rows; 197 twins), main and this branch produced byte-identical
  reports (147,468 bytes of JSON), each run verified to import its own tree.
- **What it bought, measured (median of 5):** `cohort_report` 1.31 s → 1.08 s, of which 1.03 s is
  `read_candidates` verifying the candidate store's seals — untouched here; the row scan itself went from
  about 0.27 s to 0.05 s. `null_report` 0.23 s → 0.10 s. Small today; the point is that it no longer
  multiplies.
- **A trap worth knowing:** the host venv puts the primary checkout on `sys.path`, so `PYTHONPATH=<tree>`
  does not select a worktree's code — the first comparison silently ran the same old code twice.
  `sys.path.insert(0, tree)` plus an assert on `__file__` does.
