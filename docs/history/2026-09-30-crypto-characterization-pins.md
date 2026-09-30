# The lane's surprising current behaviour, pinned as it is (crypto refactor plan PR-05)

- **Why:** the refactor plan (§O) found five behaviours that a refactor could change without
  anyone noticing. Two of them are findings: S-1 (decided 2026-09-15, kept by D-4) and S-3
  (to be fixed by PR-S3, D-3). Moving code around them needs a test that fails the day the
  behaviour changes.
- **What was delivered:** `tests/test_mvp_runtime_crypto_characterization.py`, eight tests.
  - **S-1:** `/kill` and `/pause` drop the trading fire, so the live leg (settle, protect,
    time exit, reconcile) never runs. A HARD halt keeps the fire and the leg running.
  - **S-3:** a pool install built from a read taken before a LIVE-tier disarm puts the tier
    back. This is the promotion door's own read→install sequence. PR-S3 flips this test.
  - **The two streak counters:** `guards` skips probes and reads net R (`pnl_r`).
    `live_allowance` counts probes and reads `result_R` as written.
  - **The cycle's stage order:** paper, counterfactual, forward book, report, live allowance,
    live leg, lifecycle.
  - **Live trading off:** such a cycle never reads the execution stage (every
    `resolve_execution_stage` binding refuses and records). So READ_ONLY, SHADOW and PAPER
    run the paper plane identically.
- **Proved by mutation:** exempting `crypto_pipeline` from the kill-switch skip fails both S-1
  tests, and a stage read inside the cycle fails the last test (reverted).
- **Runtime change:** none. Tests only.
