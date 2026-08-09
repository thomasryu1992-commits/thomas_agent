# Crypto Live Execution — Verification Record v0.1

**Status:** Verification pass over the *ported* live-execution safety code (LP1–LP3, LP6).
**Date:** 2026-07-25.
**Authority:** None. This is a review record, not a contract or an approval. The canonical
Governance Policy (`governance/GOVERNANCE_POLICY.yaml`) owns every rule; live trading stays
OFF. See `CRYPTO_LIVE_EXECUTION_V0.1.md` for status and `LIVE_EXECUTION_GOVERNANCE_V0.1.md`
for the decisions the order path still needs.

## Why this exists

The live-execution stack was ported from the frozen `crypto_AI_System` project — a stack that
did trade live there (signed testnet FILL 2026-07-15, one mainnet canary FILL 2026-07-16). Before
building on it, the ported safety code was re-verified in this runtime. This records what was
checked and the verdict, so a later reader (or the LP4/LP5 author) inherits the evidence instead
of re-deriving it.

**Method:** direct code reading + invariant-to-code cross-check + running the test suite. No live
enablement, no real keys, no real orders — the verification touched none of that, and none of it
is reachable (the order path does not exist).

## Headline

**PASS — no fail-open path found.** The ported LP1/LP2/LP3/LP6 code is fail-closed, and every
money-safety invariant the source system relied on is enforced in this runtime *and* pinned by
tests (133 live-related tests pass). Most importantly, **no order-egress code exists** anywhere
under `runtime/mvp_runtime/crypto/` — the live surface is entirely the *refusal* machinery
(guards, P&L ledger, canary evidence, readiness board). The trading risk is structurally absent
until LP4/LP5 are deliberately written; this is not a disabled flag, it is an absent capability.

## Invariants verified against code

| Invariant | Code | Verdict |
|---|---|---|
| Unconfigured loss limit reads as **breached** (`None`/`0`/negative/non-numeric → `True`) | `live_pnl.py:196` (`daily_loss_limit_breached`) | ✅ |
| Live history is a **verified read** (self-hash, duplicate `outcome_id`/`settlement_id`, unparseable → raise; missing → empty) | `live_pnl.py:114` (`read_live_outcomes`) | ✅ |
| A non-numeric P&L amount **raises**, never read as zero (would understate a loss and clear the breaker) | `live_pnl.py:176` (`daily_realized_pnl`) | ✅ |
| `live_risk_snapshot` **fails closed** — an unreadable history reports the breaker tripped | `live_pnl.py:208` | ✅ |
| Final guard's checks **accumulate** — no short-circuit / early return; the operator sees every reason | `live_order.py:272-342` (`evaluate_live_order_guard`) | ✅ |
| A cap of `0` **blocks** ("not configured"); a cap above the absolute ceiling (`live_budget.HARD_CEILING_USDT`, 500 USDT since 2026-08-08) is **refused, not clamped** | `live_order.py:310-316` | ✅ |
| A missing notional is a **repair**, never back-filled from the cap | `live_order.py:317-318`, and `build_live_order_intent` → `MISSING_ORDER_NOTIONAL` | ✅ |
| A **declared** notional is verified against `quantity x` the venue's price rather than trusted — understating it refuses (`ORDER_NOTIONAL_UNDERSTATED`), and an absent / synthetic / stale price refuses too (`ORDER_NOTIONAL_PRICE_UNKNOWN`) | `live_order.check_declared_notional` + `market_data.read_reference_price`, wired at `scripts/place_canary_order.py` — the only door where quantity and notional are independent inputs (added 2026-07-26, #268) | ✅ |
| `approved` is `True` only when there are **no blocks and no repairs** | `live_order.py:344-347` | ✅ |
| `kill_blocks: external_execution` blocks a live **entry** (PAUSED/KILLED runtime) | `live_order.py:286` | ✅ |
| Damaged canary evidence counts as **zero** clean orders (never the last good number); `min_orders ≤ 0` refused | `live_promotion.py` (`promotion_status`, `clean_canary_order_count`) | ✅ |
| One live-trading switch (both `network_access` + `filesystem_write`); re-asserted at **every** egress | `safety_gate.py` (`assert_authorization`, `select_env_gated`); `live_pnl.py`, `live_position.py`, `live_order.py` counter, `live_promotion.py`, `live_execution.py` | ⚠️ **weakened 2026-07-28** — the switch is `MVP_LIVE_TRADING=real` alone, no grant record. The egress re-check re-reads the env, so it is no longer a mid-flight revocation; use the runtime `kill` verb for that |
| Every real writer defaults to **inert/DryRun** | each `select_*` | ✅ |
| ~~the env var alone fails closed (`ACTIVATION_MISSING`)~~ | — | ❌ **no longer true, 2026-07-28** — Thomas removed the `live_trading` grant, so `MVP_LIVE_TRADING=real` alone now builds every real writer. Split onto its own row rather than edited into the one above, because the inert default still holds and the second half no longer does |

## Credential handling (`account.py` — the only module holding real keys)

Read-only account feed; **no order method exists** on it (single-method protocol). Verified:

- API key/secret read from env **at call time**, never stored on the object; a missing credential
  is reported by **name only, never value** (`account.py:220-228`).
- The signed request URL carries the signature in its query string, so a transport failure raises
  a **deliberately generic** `TOOL_TRANSPORT` — the URL never reaches a message, log, or record
  (`account.py:245-247`).
- Authorization re-asserted at egress (`account.py:188-194`); host allowlist `fapi.binance.com`
  enforced at construction; gated on its **own** `binance_futures_account` grant (`network_access`
  only), separate from the trading grant.

**No credential-leak path found.**

## The three "off" switches are consistent

All three independently say live trading is off, and they agree:

- `ORDER_PATH_IMPLEMENTED = False` — `live_readiness.py:52` (the readiness board can therefore
  never report READY).
- `financial_transaction_execution_implemented: false` — `GOVERNANCE_POLICY.yaml:284`.
- `financial_executor_enabled: false` — `GOVERNANCE_POLICY.yaml:275`.

## Wiring: the live surface is standalone

Nothing in the autonomous pipeline / scheduler / cycle / operator imports `live_order`,
`live_pnl`, or `live_promotion`. They are reachable only via explicit CLI (`live_readiness`,
`dashboard --account`) and cross-imports among themselves. `permission.py` builds a live-order
PermissionDecision as REVIEW_ONLY evidence only. So even if a guard were wrong, no autonomous path
would reach it.

## Forward-looking checkpoints (NOT current defects — for the LP4/LP5 author)

1. **reduceOnly close guard** (`live_order.py:365` `evaluate_live_close_guard`) is deliberately
   exempt from the loss breaker, daily count, exposure cap, promotion gate, and **both** kill
   switches — a halt must not trap a losing position open. Its "can only shrink, never open"
   guarantee rests on the venue honoring `reduceOnly`. **When LP4 is built, verify the adapter
   faithfully translates `intent.reduce_only` into the venue's reduceOnly order flag** — that
   translation is the whole structural boundary. Currently harmless (nothing sends).
2. **The three off-switches must flip together** when LP4 lands. A drift where one flips and
   another does not is the dangerous state; keep `ORDER_PATH_IMPLEMENTED` and the two governance
   flags in lockstep (and the readiness board's constant must match the yaml).
3. **Test-name hygiene:** `tests/test_mvp_runtime_crypto_promotion.py` tests the *paper* strategy
   promotion, **not** `live_promotion.py`; live canary promotion is covered inside
   `test_mvp_runtime_crypto_live_readiness.py`. A dedicated `live_promotion` test file would
   remove the ambiguity (optional).

## Test evidence

`133 passed` across `test_mvp_runtime_crypto_live_guard.py` (51 — LP2 breaker + LP3 guard + intent
+ counter), `test_mvp_runtime_crypto_live_readiness.py` (28), `test_mvp_runtime_crypto_account.py`
(21), `test_mvp_runtime_crypto_promotion.py` (28, paper), plus the permission live-order subset.

## Verdict

The ported live-execution safety infrastructure is **trustworthy to build on**.

This paragraph listed LP4, LP5, the `live_trading_budget.v0.1` schema and ≥3 clean canary orders
"(currently 0)" as what remained before any live order. **All four of the build items shipped**
(LP4 2026-07-25; LP5 and its executing leg, then cycle routing, by 2026-07-28; the budget schema
at step 6). What remains is operator state, not code: the `execution.live_trader` P5 role
activated (step 7, a separate `ROLE_GOVERNANCE` approval), the live-trading opt-in, the
confirmation phrase, a registered budget, and the canary evidence — the count being per-machine,
so ask `python -m runtime.mvp_runtime.crypto.live_readiness` rather than this file.
