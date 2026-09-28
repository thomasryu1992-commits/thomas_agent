# The execution stage anti-rollback proposal: a chained ledger and an anchor the backup does not carry

- **What was found:** the stage record can be put back, and the module says so
  (`execution_stage.py:42`). Two ordinary operations undo a demotion and still pass every witness
  check:
  - an old `execution_stage.json` copy put back;
  - a whole `.runtime_governance_state/` restore from the daily govstate tar, which also restores the
    approval and control ledgers consistently.
- **What was delivered:** `docs/proposals/EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md` (DRAFT), with
  `STATUS.md` regenerated from it. It proposes:
  - a hash-chained ledger whose tip is the stage;
  - an anchor excluded from the backup;
  - a crash-safe write order that never reads above what was witnessed;
  - BOOTSTRAP as the only recovery.
- **Deliberately not done:** no code. The four decisions (D1–D4) are Thomas's. The control store's
  same restore hole (`trading_armed`, `halt_level`) is noted as a separate proposal.
