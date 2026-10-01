# `live_reconcile` pinned rule by rule (crypto refactor plan §O)

- **Why:** the plan's characterization table listed one gap for venue reconciliation: there was no
  unit test file for `live_reconcile.reconcile_positions`. Its verdicts were pinned in
  `test_mvp_runtime_crypto_live_position.py`: each drift shape refuses an entry, an unreadable
  account refuses every entry, closes stay open, float noise is not drift. Its rules were not.
- **Measured:** 18 single-rule mutants of the module were run against the 41 test files that mention
  reconciliation. 14 left all of them green:
  - the quantity tolerance's scale and basis;
  - case-insensitive side comparison;
  - a side and a quantity that both differ, named together;
  - the run's reasons as a sorted union, and the drifted symbols sorted;
  - a venue position without a symbol being ignored;
  - the unreadable record's per-book shape and run-level reason;
  - the version and time stamp;
  - which of two local records for one symbol is compared.
- **What it adds:** `tests/test_mvp_runtime_crypto_live_reconcile.py`, tests only. It also pins two
  rules that held but had no test: a local position without a symbol fails closed, and the function
  changes neither input. A changed rule now fails a named test, and changing one is a decision that
  bumps the book's kernel version.
