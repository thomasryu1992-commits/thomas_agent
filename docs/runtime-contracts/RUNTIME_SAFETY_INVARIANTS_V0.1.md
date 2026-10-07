# Runtime Safety Invariants v0.1 — what stands between the crypto runtime and an order

**Status:** descriptive, written after the H0 safety review and R2 (Thomas 2026-10-07).
**Owner:** Thomas. **Authority:** this page describes; the code and tests named on each row decide.
What is live on a given machine is answered by `python -m runtime.mvp_runtime.crypto.live_readiness`
run in `thomas-scheduler`, never by this page.

**Why this page exists.** An outside review listed safety flags such as `place_order_enabled`,
`signed_order_executor_enabled` and `external_order_submission_allowed`. None of them exists in this
repository (H0, 2026-10-07). Each row here names a check that does exist, the code that runs it, and
the test that pins it. A flag that is not in the code is not safety evidence.

## The locks, by operation

| Operation | What refuses it below the required stage | Evidence |
|---|---|---|
| **New live entry** (autonomous leg) | 1. `execution_stage.allows(PURPOSE_AUTONOMOUS)` needs `LIVE_AUTONOMOUS`. It is checked in `live_order.evaluate_live_order_guard` and `pre_order_gate.approved_profile`. 2. The armed set: arming itself needs `LIVE_AUTONOMOUS` (`promotion.py`, `PURPOSE_LIVE_ARM`). 3. Since R2, the adapter's own egress: `live_execution.stage_refusal`. | `tests/test_mvp_runtime_crypto_execution_stage*.py`, `tests/test_mvp_runtime_crypto_egress_stage.py` |
| **Slippage probe** (manual entry) | `PURPOSE_PROBE` needs `LIVE_AUTONOMOUS`, at the guard and (R2) at egress. | `tests/test_mvp_runtime_crypto_probe.py`, egress-stage tests |
| **Any order that could add exposure on mainnet, whoever the caller** | R2: `BinanceFuturesOrderAdapter.submit` refuses with `ORDER_STAGE_REFUSED` unless the stage admits a live entry. An unreadable stage reads `READ_ONLY` and refuses. A request whose shape does not prove it reduces is treated as adding exposure. | `tests/test_mvp_runtime_crypto_egress_stage.py` |
| **Signed testnet order** | `PURPOSE_TESTNET` needs `SIGNED_TESTNET` (`testnet_execution.evaluate_testnet_order_guard`). It also needs its own switch and key pair (`MVP_TESTNET_*`). The host allowlist is `testnet.binancefuture.com` only. | `tests/test_mvp_runtime_crypto_testnet_path.py` |
| **Stage promotion** | Only `scripts/register_execution_stage.py` CLIMB, with a Thomas `/approve`. No rung may be skipped (`EXECUTION_STAGE_SKIP_REFUSED`). `LIVE_AUTONOMOUS` needs testnet evidence. No scheduled kind writes the stage. | `tests/test_mvp_runtime_crypto_execution_stage*.py` |
| **Live canary / live scaled** | `LIVE_AUTONOMOUS` / `LIVE_SCALED` rungs. Unreachable from `PAPER` without two approved climbs. | same |
| **Emergency exposure reduction** | A separate, intentional capability. It needs `reduceOnly` or `closePosition` in the built request (`order_request.is_protective_request`, venue-enforced). It also needs `MVP_LIVE_TRADING=real` and the confirmation phrase (`live_order.evaluate_live_close_guard`). It reads no stage and no control state, by design (EXECUTION_STAGE_V0.1), so a halt cannot strand a position. The scripted emergency close also needs a HARD halt and a single-use Thomas approval (`scripts/emergency_close.py`). R2 does not widen it. | `tests/test_mvp_runtime_crypto_hard_halt_egress.py`, `tests/test_mvp_runtime_crypto_emergency_close.py`, egress-stage tests |
| **Cancel** | Its own operation: it removes a resting order and adds no exposure. It is called only to withdraw a closed position's bracket legs (`live_leg.cancel_bracket_legs`) and by the testnet cycle. It reads no stage. | egress-stage tests |
| **Any mainnet write at all** | `MVP_LIVE_TRADING=real` selects the write-capable adapter (`live_execution.select_order_adapter`, env-only gate). Unset, it selects the dry-run adapter, which opens no socket. | `tests/test_mvp_runtime_safety_gate.py` |
| **Credential placement** | The order key and the account key (one venue key) reach `scheduler` only. Since H1-a, `scheduler-maint` receives nothing named `BINANCE_ACCOUNT_*`, `MVP_LIVE_ORDER_*` or `MVP_TESTNET_ORDER_*`. | `tests/test_deployment_env_passthrough.py` |

## At `PAPER`

- New live entry: **unreachable.** Three independent refusals: the guard, the armed set, and egress (R2).
- Signed testnet order: **unreachable.**
- Automatic stage promotion: **absent.**
- Live canary and live scaled: **unreachable.**
- Emergency exposure reduction: **a separate, intentional capability.** It works only while
  `MVP_LIVE_TRADING=real` and only for an order that cannot add exposure.

## Machine state at the time of writing (not an authority; re-read `live_readiness`)

On 2026-10-07 (R1), Thomas closed the live gate on this machine (`MVP_LIVE_TRADING` unset, scheduler
recreated). Before the change there were no open positions and no resting orders, read from the
venue. The trading process recorded the gate `CLOSED` at 2026-10-07T16:58:52Z.

Closing the gate also turns off the venue reader. `list_resting_orders` and the venue-contract check
are behind the same switch, so resting orders can no longer be read from the venue on this machine.
Re-opening the gate is Thomas's act and needs a restart. Read `live_readiness` and check positions
first, because the close path needs the gate.
