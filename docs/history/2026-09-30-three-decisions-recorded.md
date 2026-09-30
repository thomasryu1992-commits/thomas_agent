# Thomas decided three proposals as recommended (2026-09-30), recorded

- **`EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md`**: D1 a (the anchor in the state directory, excluded from
  the backup), D2 (the chained ledger replaces the file), D3 a (genesis by BOOTSTRAP at PAPER), D4
  (accept that a restore needs BOOTSTRAP).
- **`PROTECTION_UNKNOWN_ESCALATION_V0.1.md`**: D1 (30 / 60 minutes, missing bracket ids straight to
  U1), D2 (the runtime may tighten to HARD, raise-only), D3 (never close on UNKNOWN), D4 (a separate
  watch store).
- **`SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md`**: D1 C (LIVE requires FORWARD_CONFIRMED), D2
  (the forward bar corrected by `observed_lineages`), D3 B now and A at the epoch boundary, D4 (B is a
  bug fix under review D3).
- **What changed:**
  - The three status lines now read DECIDED, each with a decision section naming what remains to
    build.
  - `STATUS.md` is regenerated.
  - `REMAINING_WORK.md` §L gains E3 (the validation slice).
- **Deliberately not done:** no code. Each build is its own PR. The stage ledger's genesis BOOTSTRAP
  and the backup exclude are operator steps after its deploy.
