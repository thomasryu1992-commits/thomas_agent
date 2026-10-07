# R2: the mainnet order adapter reads the execution stage at egress

- **What this PR changes:**
  - `live_execution.stage_refusal`, called in `BinanceFuturesOrderAdapter.submit` after `control_refusal`.
    An order whose built shape cannot add exposure (`is_protective_request`: `reduceOnly` true, or a Close-All
    `closePosition`) is never refused and never reads the stage. Any other order needs a stage that admits a
    live entry (`LIVE_AUTONOMOUS`); otherwise it is refused before signing with `ORDER_STAGE_REFUSED`.
    That code is a nothing-sent error: `submit_and_reconcile` raises `SubmitRefused` and does not reconcile.
  - `docs/runtime-contracts/RUNTIME_SAFETY_INVARIANTS_V0.1.md`: the safety matrix under real names.
  - `docs/DIAGNOSTIC_CODE_INDEX.md`, regenerated.
- **Why:** the H0 review (2026-10-07) traced every caller. The stage was judged only in the decision layer
  (`evaluate_live_order_guard`, `pre_order_gate`). The adapter checked the control state alone. No caller
  bypassed the guard, so PAPER could not reach an entry, but nothing at egress would stop a future one.
  Thomas approved R2 as defense in depth.
- **What it does not change:**
  - The close guard, settlement, protection, the time exit and the emergency close stay stage-free.
    EXECUTION_STAGE_V0.1 requires that; a test pins that an exit never even reads the stage.
  - The control state, the guard verdict, the armed set, the budget and the breakers still apply at every
    stage. Tests pin a KILLED runtime, a HARD halt and an unapproved verdict at `LIVE_AUTONOMOUS`.
  - Cancel is its own operation and reads no stage.
  - The testnet adapter is untouched: its own guard requires `SIGNED_TESTNET`.
  - No new order path, script or endpoint.
- **Fail-closed:** a stage that cannot be read reads `READ_ONLY` and refuses. A request shape that does not
  prove it reduces (`reduceOnly: "true"` as a string, a missing flag, `closePosition: true` as a bool) counts
  as adding exposure.
- **Tests:** `tests/test_mvp_runtime_crypto_egress_stage.py`, 27 tests. With the egress call removed, 15 of
  them fail. Three adapter test files now read the stage as `LIVE_AUTONOMOUS` through
  `tests/_helpers.live_stage_at_egress`, because they test transport, signing, breakers and halts rather
  than the stage.
- **On this machine:** no runtime effect while the live gate is closed (R1, 2026-10-07). The adapter is not
  selected, so this egress is not reached.
