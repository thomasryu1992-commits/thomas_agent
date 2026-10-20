# E2: trade opening dates per regime; the proposer's rationale rides onto trial rows

- **Why now:** both waited on D3, which Thomas lifted on 2026-10-20 (stage 2 of the target architecture, run in
  order and checked for applicability).
- **E2** (`REMAINING_WORK.md` §L):
  - **What was built.** Each replayed trade carries `opened_at_utc`, its entry bar's close.
    `regime_breakdown.entry_dates` lists the distinct UTC dates trades opened on, per entry regime.
  - **No episode definition is baked in.** The 2026-09-27 record showed episode counts move several-fold with the
    definition, and dates let any definition be applied later.
  - **Nothing judges on it.** `regime_breadth`, verdicts and doors are unchanged.
  - **Why a sibling of `per_regime`.** `per_regime` is copied into pool entries and is part of the approval
    artifact's projection.
  - **New mints only.**
- **Rationale** (`CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md` §7):
  - **Built.** The trial queue and `trial_source` carry the proposer's one-sentence rationale.
  - **Not done.** The per-template rationale field across 44 templates was left out. It adds traceability only, and
    the sentences would have to be copied family by family from builder comments rather than written fresh.
- **Checked:**
  - Tests cover the dates' shape and consistency with `per_regime`, opening before closing on real replays, the
    admission projection staying date-free, and rationale on the queue and the trial row.
  - Mutation checks: dropping the dates, stamping the exit time instead of the entry time, or dropping the
    rationale at either hop each fails a test.
