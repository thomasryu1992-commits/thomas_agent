# The forward book names a pool entry it cannot walk, instead of skipping it silently

- **Why:** system review C4 (phase 0-4). A forward walk that meets a stored spec it can no longer
  parse did `except Exception: continue`. That lineage then accrues no forward evidence, and
  nothing records that it stopped. #980 fixed the cohort, null and trial walks. It left
  `forward_book` alone on purpose, because that walk runs inside the cycle that feeds the live
  leg. This entry finishes the item on that path, on the condition #980 implied: it may only
  observe.
- **What changed:**
  - `run_forward_book_update` collects the unparseable entries' strategy ids into
    `unparseable` on its summary. It still skips them and still never raises.
  - `run_crypto_cycle` adds `FORWARD_SPEC_UNPARSEABLE` to the cycle's `reason_codes`. Those codes
    are read by the dashboard. The live leg keeps its own `live_reason_codes`.
  - `cycle_status_line` prints `forward_unparseable=<ids>`, only when there are any.
- **Observational, pinned:** a new cycle test runs the same cycle twice, with and without an
  unparseable entry in the forward summary. The code appears only in the second run. The
  verdict, the route and the opened position are the same in both.
- **Tests:** four cases across the forward book, the cycle and the status line. Hand mutations
  were each caught: dropping the reason code, dropping the name in the forward book, and hiding
  the status token.
- **Deliberately not done:** no board row, no alert, and no change to which entries are walked.
  The entry is named, not repaired or retired. What to do about an unparseable spec stays a
  pool decision.
