# LP5 Position Kernel + Cycle Routing — Design Record v0.1

**Status:** PARTIALLY IMPLEMENTED — LP5.1 (position state + reconciliation), LP5.2 (sizing),
LP5.4 (the outcome bridge) and LP5.3's *decision* half (`live_entry.plan_live_entry`) all merged
2026-07-25. **The executing leg + cycle routing remain unbuilt**, and have their own record:
`LP5_3_LIVE_LEG_DESIGN_V0.1.md`. Kept as the decision trail; current truth is
`CRYPTO_LIVE_EXECUTION_V0.1.md` and the code.
**Owner:** Thomas
**Authority:** None. `governance/GOVERNANCE_POLICY.yaml` owns every rule. Since LP4 an order
path exists (`financial_transaction_execution_implemented: true`, `ORDER_PATH_IMPLEMENTED = True`)
— but nothing routes to it autonomously, which is exactly what the unbuilt executing leg would
change. `financial_executor_enabled` stays false.
Sequenced after LP4 (`LP4_ORDER_ADAPTER_DESIGN_V0.1.md`, complete).
Depends decisions: `LIVE_EXECUTION_GOVERNANCE_V0.1.md`. Verification baseline:
`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md`.

**Claude does not run this, does not handle real keys, and does not enable live trading.**

## What LP5 is

The **live position kernel**: the state and the decisions *around* an order. LP4 sends one order;
LP5 decides that a position should exist, sizes it, tracks it while it is open, decides it must
close, and records the realized outcome. Plus the **cycle routing** that lets the crypto cycle run
a live leg at all (today the cycle is paper-only).

```
route_entries (existing, pure)          ── one ranked entry plan per context
        │
        ▼
   [LP5] size_live_order ──────────────── min(risk-based, budget cap); refuse if unsizable
        │
        ▼
   resolve_live_order_limits + evaluate_live_order_guard (existing)
        │ PASS
        ▼
   [LP4] submit_and_reconcile (entry MARKET)
        │ RECONCILED
        ▼
   [LP5] place the protective bracket (venue-side SL + TP, reduceOnly)  ◀── decision 1
        │            └── bracket fails ⇒ close the naked position immediately
        ▼
   [LP5] open the live position in live_positions/ (stage: "live")
        ⋮  (later cycles)
   [LP5] reconcile against the venue ── drift ⇒ refuse to trade this book
        │
        ▼
   [LP5] exit decision ── reduceOnly close via LP4 ── realized PnL from ACTUAL FILLS
        │
        ▼
   live_pnl.build_live_outcome_record → RealLiveLedger.append_outcome (existing)
```

## What LP5 is NOT

- It does **not** submit orders — that is LP4's single job.
- It does **not** invent permission: every entry still passes `evaluate_live_order_guard`, every
  close `evaluate_live_close_guard`.
- It does **not** replace the paper kernel. Paper keeps running, in its own state namespace.

## Three asymmetries with the paper kernel (why LP5 is not a copy)

| | paper (today) | live (LP5) |
|---|---|---|
| **Truth about a position** | the store — `open_position` mints a record with no order id, no fill, no proof | **the venue** — `account.AccountSnapshot.positions` is authoritative; local state must reconcile to it |
| **Prices / PnL** | modelled: entry = `feature_row["close"]`, exit = the SL/TP *numbers*; result is `result_R` | **actual fills**; `realized_pnl_usdt` from what the venue really executed (slippage + fees included) |
| **Sizing** | **does not exist** — no `quantity`, no notional anywhere (`paper.py` states this deliberately) | required; `build_live_order_intent` refuses to invent either |

## ⚠️ State separation is mandatory, not stylistic

Paper positions are keyed `(venue, symbol, timeframe)` where `venue` defaults to
`"binance_futures"` — **the same string a live position would carry** — and
`list_open_positions` globs every `*.json` in `positions/`. The position record has **no `stage`
field**. So if LP5 reused that directory:

> a live BTCUSDT/1d position and a paper one collide on one file, and the **paper cycle could
> settle a real position with simulated math and mark it CLOSED** — or refuse with
> `POSITION_CONTEXT_MISMATCH` and strand it.

LP5 therefore uses a **distinct namespace** — `live_positions/` — and stamps every live record
`stage: "live"`. Related standing tripwire: `paper.py` documents that its `MAX_CONCURRENT_POSITIONS
= 20` / `MAX_POSITIONS_PER_SYMBOL = 4` caps are acceptable *only because* live and paper share no
code, and mandates reverting to the derived value before a paper book is used to size live
exposure. LP5 keeps the books fully separate and defines its **own, much smaller** live caps
rather than inheriting 20/4.

## The venue is the truth: reconcile-or-refuse

Paper has no reconciliation because it cannot drift. Live can: a partial fill, a venue-side stop,
a liquidation, or a manual close on the phone all change the position without the runtime knowing.
So every live cycle **starts from an account read** and compares:

- local open book vs `AccountSnapshot.positions` (symbol, side/sign, quantity, notional);
- a match ⇒ proceed;
- **any drift, or an unreadable account ⇒ that book is refused for new entries** and surfaced.
  Closes stay permitted (a halt must not trap a position).

This is the LP4 reconcile posture applied to state rather than to one order.

## Decision 1 — the protective bracket at entry (Thomas, 2026-07-25)

**Both SL and TP are placed as venue-side reduceOnly orders at entry time.** Rationale: paper's
stop is a number in a JSON file checked only when the cycle fires (for a 1d context, once a day).
Live has no such courtesy — between fires, price can pass the stop and keep going, and the
daily-loss breaker only blocks *new entries*, it never closes an open one. A venue-side bracket
removes the unprotected window entirely.

What this costs, stated plainly:

- **LP4 must be extended** beyond MARKET: conditional order types (`STOP_MARKET`,
  `TAKE_PROFIT_MARKET`) with a `stopPrice`, and `reduceOnly`. `build_order_request` currently
  asserts `order_type_exchange == "MARKET"`.
- **Order-count cap semantics need a decision.** The registered budget's
  `max_daily_order_count` is 2. One bracketed position is 3 venue orders, which would exhaust
  that instantly. **Recommendation:** the counter counts **entry** orders only — an entry is the
  risk-taking decision; protective orders and closes only *reduce* risk. This follows the existing
  precedent that `evaluate_live_close_guard` already exempts the daily count. To be recorded
  explicitly, because "2 orders/day" read against the venue's own order count would look different.
- **Reconciliation widens** to the protective orders themselves: a position whose stop order is
  missing is a **naked position**. LP5 treats that as a close-now condition (below), not a warning.
- **Cancel on close.** When a position closes (TP fills, SL fills, or the cycle closes it), the
  surviving protective order is cancelled. Safety net if a cancel is missed: a leftover *reduceOnly*
  order cannot open anything — it can only reduce — so a stale one is a nuisance, not a new risk.

**Naked-position rule:** if the entry fills but a protective order cannot be placed, LP5
**immediately closes the position** (reduceOnly market) rather than leaving it unprotected. An
unprotected live position is precisely what the bracket exists to prevent, so the fail-closed
direction is out, not in.

## Decision 2 — sizing: `min(risk-based, budget cap)`, refuse if unsizable (Thomas, 2026-07-25)

`size_live_order` is the one genuinely new piece with no paper precedent:

1. **risk-based candidate** — from `plan.risk` (the entry↔stop distance `build_entry_plan` already
   computes) and the account equity, for a fixed risk per trade.
2. **budget candidate** — `max_order_notional_usdt` from the registered budget (currently 60 USDT).
3. **take the smaller.** Then round to the venue's lot-size / min-notional steps.
4. **Refuse rather than default**: no equity read, no `plan.risk`, a rounded size of zero, or a
   result outside the budget ⇒ no order (`build_live_order_intent`'s posture).

Why `min()` here is not the "clamping" the repo forbids: what is forbidden is the *guard* silently
resizing an approved order, which would desync the size from the decision that approved it. Here
the size is decided **before** the guard, and `evaluate_live_order_guard` then independently
verifies the final notional against the cap and the absolute ceiling. Consistent by construction.

## Open exposure — from the venue, and the one fail-open default gets closed

`evaluate_live_order_guard(current_open_notional_usdt=0.0)` defaults to `0.0`, and its only caller
today (the readiness board) passes `0.0` literally. That is the **single fail-open default** in an
otherwise fail-closed guard — a caller that forgets the argument silently disables the exposure cap.

LP5 supplies it truthfully by summing `AccountPosition.notional` across the venue's open positions
(the read-only account key, distinct from the order key). Fail-closed: an unreadable account means
exposure is **treated as at-cap**, never as zero. The argument should also stop defaulting.

## The close path keeps its exemptions

Per the standing decision, a reduceOnly close is exempt from the loss breaker, the caps, the daily
count, the promotion gate, and both kill switches — a halt that traps a losing position open is
worse than the halt prevents. LP5 preserves that: **closes run even when entries are fully halted**,
and the structural boundary (grant + confirmation phrase + `reduce_only`) is what keeps that path
unable to open anything.

## Cycle routing

- A live leg in the crypto cycle (or a sibling `run_live_update`), kill-switch bound via
  `ControlStore` like the paper step, settle-then-maybe-open ordering, per-book + portfolio locking.
- **Route once, share the result.** `route_entries` is already called twice per cycle (the paper
  step and the counterfactual shadow); adding a live leg naively would make three evaluations of
  the same pool row and risk the legs disagreeing.
- Audit events on the live leg carry `external_action: True` (the paper step hard-codes `False`).
- **Fan-out stop is stricter for live.** `run_pool_cycle` currently swallows every non-kill error
  into `skipped`. For live, an unreconcilable position is a portfolio-level reason to stop, not a
  row in a skip list.
- The `crypto_cycle` record gains live fields (`live_route_status`, `live_opened`, `live_settled`,
  `live_reason_codes`) so the ledger shows live activity.

## A gap worth naming: the guards cannot read live outcomes

The live outcome record has no `result_R`, no `created_at_utc`, and no strategy lineage, so
`guards.run_risk_guard`, `lifecycle`, and the C6 feedback report — all of which key on those —
are **blind to live results** today. Only the daily-loss breaker (which reads
`realized_pnl_usdt`) works. LP5 needs either an additive live-outcome shape carrying those fields
or an adapter. Until then a live loss streak would not demote a strategy the way a paper one does.

## Live caps (LP5's own, not paper's)

Small and explicit: a live concurrent-position cap and per-symbol cap far below paper's 20/4, plus
the existing per-order / daily-count / open-exposure / daily-loss caps from the registered budget.

## Must verify at implementation time (real money — do not assume)

- **Exact Binance USDT-M conditional-order semantics**: `STOP_MARKET` / `TAKE_PROFIT_MARKET`,
  `stopPrice` vs `activationPrice`, `reduceOnly` vs `closePosition`, whether a protective order is
  auto-cancelled when the position closes, and whether both legs can coexist. Verify against the
  venue's current docs; do not implement from memory.
- **Lot size / min notional / price tick** (`exchangeInfo`): an unrounded size or stop price is
  rejected outright.
- **Position mode**: one-way vs hedge (`positionSide`).
- **Fee and funding accounting** for realized PnL — the venue's income endpoint is the honest
  source, not a computed `(exit - entry) * qty`.

**Bracket shape note added 2026-07-28 (maker take-profit).** The two legs are no longer the same
shape. The stop stays a `closePosition` `STOP_MARKET`; the target became a **sized `reduceOnly`
`LIMIT`**, which earns the maker rate instead of triggering into a market order at a price the
market had already reached. The venue documents `closePosition` for the two `_MARKET` conditional
types **only**, so the target leg cannot be Close-All and must carry a quantity — it is sized
from the ACTUAL entry fill, never the intent, or a partial fill would leave it resting to reduce
more than exists. The asymmetry runs in the safe direction: the stop still covers whatever is
open. `cost.DEFAULT_MAKER_FEE_BPS` is the venue's **published** 2.0 bps and is **not yet measured
on this account** — the first live maker fill should replace it with a measurement, because an
understated maker rate reports an edge better than reality.

## Open engineering decisions (deferred to implementation, not blockers)

- Exit-decision cadence: keep the paper shape (decide on the last closed candle at cycle
  cadence) now that the venue-side bracket covers the tail, or add a faster live tick.
- Where the risk-per-trade fraction lives (a budget field vs a constant).
- Whether the live book stores one position per `(symbol, timeframe)` like paper, or one per
  symbol (the venue nets per symbol in one-way mode — the venue's shape should win).

## Proposed implementation shape (increments, once this design is accepted)

1. **LP5.1 — state + reconciliation (no orders):** `live_position.py` (record with `stage`,
   quantity, notional, order ids, fill price), `LivePositionStore` (DryRun default, gated on
   the live-trading switch, locked, atomic), `reconcile_positions` against the account snapshot,
   `compute_open_notional_usdt`, and the fail-open `0.0` default closed. Fully testable with no
   network and no venue.
2. **LP5.2 — sizing:** `size_live_order` (min-of-two, venue rounding, refuse-not-default).
3. **LP5.3 — the live leg:** `run_live_update` + cycle routing + the cycle record fields, still
   with LP4's real send stubbed, so no order can be sent.
4. **LP5.4 — the outcome bridge:** live outcomes readable by the risk guard / lifecycle / feedback.
5. **After LP4 increment 2 only:** the bracket order types, then the first supervised live cycles.

## What still stands between LP5 and an autonomous live order

Merging LP5 authorizes nothing. Still required: `MVP_LIVE_TRADING=real` + the order key, the
confirmation phrase, a valid registered budget, the kill switch ACTIVE, a guard PASS, **≥ 3 clean
canary orders**, and the `execution.live_trader` role **activated** (a separate
`ROLE_GOVERNANCE` approval).

This list carried "(currently 0)" against the canary count and "and LP4 increment 2 (the real
signed send)" until 2026-07-29. Both are gone rather than updated: LP4's signed send shipped
2026-07-25, and the canary count is **per-machine state** that no committed file can report.
Ask `python -m runtime.mvp_runtime.crypto.live_readiness`.
