# H1-c: the testnet adapter checks the execution stage at its own egress

- **What this PR changes:**
  - `crypto/testnet_execution.py`: `BinanceTestnetOrderAdapter.submit` now refuses an order that could
    add exposure, with `ORDER_STAGE_REFUSED`, unless the execution stage admits a signed testnet order
    (`SIGNED_TESTNET` or above). It runs after the control state check (`ORDER_HALTED`), which keeps its
    own code and order.
  - `crypto/live_execution.py`: R2's `stage_refusal` takes the purposes whose stage the venue's
    entries need, and the venue name for its message. The default is unchanged, the live entry purposes
    for mainnet. The testnet adapter passes `TESTNET_ENTRY_PURPOSES = (PURPOSE_TESTNET,)`.
  - Tests: testnet cases in `tests/test_mvp_runtime_crypto_egress_stage.py`. One testnet-path test now
    sets the stage so that the venue's answer is what it judges. Docs: RUNTIME_SAFETY_INVARIANTS and
    the diagnostic index.
- **Why:** R2 (2026-10-07) gave the mainnet adapter a last stage check for a caller that reaches it
  without the guard. The testnet adapter had only the control state check. The testnet guard
  (`evaluate_testnet_order_guard`) judges the stage, but a new path or a regression that skipped it
  would have met no stage at the adapter. H1-c was planned as the testnet half of R2.
- **Shaped this way because:**
  - **One function, two thresholds.** The check is R2's own `stage_refusal` with the testnet purpose,
    so there is no second copy of the rule. The threshold is the decision layer's: `required_stage`
    of `PURPOSE_TESTNET`.
  - **Exits and protection never read the stage.** This is the same exposure test as R2
    (`is_protective_request`), so an unreadable stage cannot strand a testnet position.
  - **A refusal is a nothing-sent error.** `submit_and_reconcile` already raises `SubmitRefused` for
    `ORDER_STAGE_REFUSED`, so a refused testnet cycle has nothing to reconcile.
- **What it deliberately does not do:** it does not restructure the testnet path, change the testnet
  guard, add a gate or touch the testnet key placement. At PAPER, a signed testnet order now meets two
  refusals instead of one.
