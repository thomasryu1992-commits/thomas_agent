# The execution stage is the tip of a hash-chained ledger, vouched for by an anchor the backup does not carry

- **Decision:** Thomas 2026-09-30, `EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md`. D1 a (the anchor in the
  state directory, excluded from the backup), D2 (the ledger replaces the file), D3 a (genesis by
  BOOTSTRAP), D4 (a restore needs a BOOTSTRAP).
- **Why:** a stage record's witness (a CONSUMED approval) stays valid forever, so an older copy put
  back, or the whole state directory restored from the daily backup, undid a demotion and passed every
  check. The module said so itself.
- **What changed:**
  - `crypto/execution_stage_ledger.jsonl` is append-only and hash-chained, and its tip is the stage.
    Every read verifies the whole chain: row hashes, `seq`, links, stage continuity, and no approval
    witnessing two rows.
  - `crypto/execution_stage_anchor.json` names the row last reached. A chain it does not vouch for
    reads READ_ONLY: ROLLED_BACK (a restored ledger beside the newer anchor), ANCHOR_MISSING (wiped
    and restored), ANCHOR_TAMPERED, or ANCHOR_BEHIND (more than one row).
  - `execution_stage.json` is now a mirror no decision reads.
  - Writes go row → anchor → mirror. A failed anchor write is a warning, and `--sync-anchor`
    (no approval; it cannot change the stage and refuses a missing, tampered or off-chain anchor)
    re-anchors.
  - An unextendable chain is replaced, never edited, and only by a BOOTSTRAP or a demotion to
    READ_ONLY. The door checks that a record can land before it spends the approval.
  - Two new closed schemas: `execution_stage_ledger.v0.1` and `execution_stage_anchor.v0.1`.
  - `scripts/ops/harness_backup.sh` excludes the anchor, and `backup_watch.sh` reports an archive that
    carries it.
- **Tests:**
  - 18 in `test_mvp_runtime_crypto_execution_stage_ledger.py`: the copy put back, untar-over,
    wipe-and-restore, BOOTSTRAP as the only way up, a crash at each step, sync, the sync refusing
    what a restore leaves, chain breaks, a replayed approval, and genesis from the old file.
  - The tamper tests now forge the ledger's tip and anchor instead of the file.
  - Two watch tests. Both are POSIX-only (the watch is a bash script run on the host), so the
    Windows skip ceiling in `tests/skip_ceiling.json` moves from 94 to 96 in the same change.
- **After the deploy (operator):**
  - Install the backup script on the host.
  - Then BOOTSTRAP at PAPER: one Thomas approval writes row 0 and the anchor.
  - Until then the machine reads READ_ONLY (LEDGER_MISSING). At PAPER no door that reads the stage is
    affected.
