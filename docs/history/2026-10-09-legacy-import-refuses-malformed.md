# The pre-H6d import refuses a cache that does not say what H6b meant

- **What this PR changes (`holdings/cash_flows.py`):**
  - `_legacy_fields` checks the pre-H6d fields before they become the one `legacy_state_imported` line:
    - Toss counters must be whole numbers ≥ 0;
    - `settlement_days` must be a mapping of KST dates to rows of such counters;
    - the shadow dates must be a list of KST dates;
    - the first verified fire must be a time.

    Anything else is refused with the existing `HOLDINGS_CASH_FLOW_LEDGER_TAMPERED`, the code an
    unreadable state file already gets. A missing field still reads as empty, as before.
  - The refusal is raised inside the ledger write, so neither the ledger nor the state file changes. A
    retry refuses the same way. Once the cache is repaired, the import happens once.
  - `readiness`: a pending import that would be refused fails `ledger_state_consistency`.
  - `toss_semantics`: before the import, the counters on file are read through the same check. Malformed
    ones answer `WAITING`, never `PASS`.
- **Why:** this was found in the H6d pre-deploy verification on synthetic data. The import coerced with
  `int(...)`:
  - a string or a list stopped the writer with an untyped `ValueError` or `AttributeError`;
  - **a negative, boolean or fractional count, a day that is not a date, a string of dates and a first
    fire that is not a time were imported as they stood**, into an append-only ledger that cannot take
    them back. Before the import, the same cache was read unchecked by the Toss verdict.
- **Production:** not affected. H6d (`candidate-1205`) was deployed at 2026-10-09 15:30Z. Its first fire
  (15:33:53Z) imported the H6b cache that the H6b code had written: `ledger_state_consistency` PASS,
  `cutover_boundary` PASS, one verified date kept. The change is for any later import from a cache that
  is not well formed.
- **Tests (`tests/test_mvp_runtime_holdings_h6d_ledger.py`):**
  - A well-formed cache is imported once.
  - Twelve malformed shapes are refused with the code; both files stay byte-for-byte unchanged across a
    retry, and the readiness is FAIL.
  - A repaired cache is imported once.
  - Against the previous code, all 13 new failure tests fail, and the well-formed one passes.
