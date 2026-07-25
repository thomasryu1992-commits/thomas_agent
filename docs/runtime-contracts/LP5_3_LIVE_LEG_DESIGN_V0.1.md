# LP5.3 — The Executing Leg + Cycle Routing: Design Record v0.1

**Status:** Design record for the **remaining half** of LP5.3. **No code; nothing here enables
trading.** This is the proposal to review before the executing leg is written.
**Owner:** Thomas
**Authority:** None. `governance/GOVERNANCE_POLICY.yaml` owns every rule.

> **Read this first.** Every other increment in this stack could be described as plumbing. This
> one cannot. LP4 shipped an order path and `financial_transaction_execution_implemented` is
> `true`; LP5.1/5.2/5.4 shipped state, sizing and the outcome bridge; LP5.3's *decision* half
> (`live_entry.plan_live_entry`) shipped too. **The executing leg and the cycle routing are the
> piece that makes an autonomous live order structurally reachable.** Today the only door is
> `scripts/place_canary_order.py`, one deliberate canary at a time, and a test
> (`test_no_autonomous_entry_point_reaches_the_live_order_path`) fails loudly if any autonomous
> entry point starts importing the order path. Building this is the decision to remove that.

## What already exists (and must not be rebuilt)

| Piece | Module | What it gives the executing leg |
|---|---|---|
| Position state + reconciliation | `live_position.py` | `reconcile_positions` (RECONCILED / DRIFT / ACCOUNT_UNREADABLE), the `live_positions/` book with `stage: "live"`, `live_capacity` (2 open / 1 per symbol), `compute_open_notional_usdt` |
| Sizing | `live_sizing.py` | `size_live_order` — `min(risk-based, budget cap)`, venue-filter floored, refuses rather than defaults |
| The decision | `live_entry.py` | `plan_live_entry(...) -> {status, intent, sizing, bracket, guard}`; `READY` only when the final guard approved. **Holds no adapter and imports none** |
| The send | `live_execution.py` | `submit_and_reconcile` (entry MARKET + the conditional bracket types), reconcile-first |
| The outcome | `live_pnl.py` | `build_live_outcome_record`, the ledger, the daily-loss breaker |

**So LP5.3's remainder is genuinely small in code and large in consequence**: it is the wire
between a decision that already exists and a sender that already exists.

## What the executing leg does

```
plan_live_entry(...) -> READY          (already built, pure, no I/O)
        │
        ▼
  [NEW] execute_live_entry
        │  1. submit the ENTRY (submit_and_reconcile, MARKET)
        │     └─ not RECONCILED ─▶ record, open nothing, stop
        │  2. place the protective BRACKET (SL + TP, reduceOnly, from decision["bracket"])
        │     └─ either leg fails ─▶ CLOSE THE NAKED POSITION IMMEDIATELY (reduceOnly market)
        │  3. open the live position in live_positions/ with the ACTUAL fill
        ▼
  [NEW] execute_live_exit  (on a later cycle)
        │  1. decide the close (or observe the venue already closed it)
        │  2. reduceOnly close via submit_and_reconcile
        │  3. CANCEL the surviving bracket leg   ← the venue documents no auto-cancel
        │  4. realized PnL from ACTUAL fills -> build_live_outcome_record -> ledger
        ▼
  [NEW] cycle routing: a live leg in run_crypto_cycle
```

### Three rules the executing leg owes

1. **Open only on `RECONCILED`.** A submit that returned `MISMATCH`, `NOT_FOUND` or
   `UNRECONCILABLE` must not create a local position. The venue is the truth; an unconfirmed
   entry is not a position, it is an incident to surface.
2. **A naked position is closed, not warned about.** If the entry fills but a bracket leg cannot
   be placed, close immediately (reduceOnly). An unprotected live position is precisely what the
   bracket exists to prevent, so the fail-closed direction is *out*, not in.
3. **Cancel the surviving leg on close.** The venue documents **no** auto-cancel for conditional
   orders when a position closes (verified 2026-07-25). A leftover *reduceOnly* order cannot open
   anything — it can only reduce — so a missed cancel is a nuisance rather than a new risk, but
   leaving them accumulates and makes reconciliation harder to read.

## Cycle routing — the shape

- **Route once, share the result.** `route_entries` is already evaluated twice per cycle (the
  paper step and the counterfactual shadow). A live leg must reuse that result, not add a third
  evaluation that could disagree with the other two.
- **Reconcile before anything.** The live leg starts from an account read →
  `reconcile_positions`. Anything but `RECONCILED` refuses **entries** for that book while
  **closes stay allowed** — being unable to see the account must never trap an open position.
- **Kill-switch bound**, like the paper step, and audited with `external_action: True` (the paper
  step hard-codes `False`).
- **A live failure is portfolio-level.** `run_pool_cycle` currently folds any non-kill error into
  `skipped` and moves to the next context. For a live leg that is wrong: an unreconcilable
  position or a naked-position close is a reason to stop the whole fan-out, not a row in a skip
  list.
- **The cycle record** gains `live_route_status`, `live_opened`, `live_settled`,
  `live_reason_codes` so the ledger shows live activity distinctly from paper.

## Two things this increment owes the rest of the system

- **Readiness board exposure.** `live_readiness` currently reports open exposure honestly as
  *at-cap* when it cannot read the account (LP5.1c closed the fail-open). With the live leg
  reading the account every cycle, the board should surface the real figure so that honest
  block-at-cap can lift.
- **The guards/lifecycle bridge is already there** (LP5.4), so live outcomes feed the risk guard
  and lifecycle — but note the guard is scoped to `mvp_paper_kernel` outcomes today (Thomas,
  2026-07-25). **Live outcomes must be added to that scope when the live leg lands**, or the
  breaker will ignore real losses. This is a one-line change with a large consequence and is
  called out here so it is not discovered afterwards.

## What this increment does NOT do

- It does not mint a grant, a key, a phrase, or a budget.
- It does not activate the `execution.live_trader` role (candidate, non-routable — its own
  `ROLE_GOVERNANCE` approval).
- It does not place a canary; ≥ 3 clean canaries remain a precondition of the autonomous path and
  the guard enforces it (a canary is exempt from that gate; the autonomous path is not).

## Preconditions before this is built — not merely before it is enabled

Stated plainly, because "the code exists but is gated" is a weaker safety story than "the code
does not exist", and this increment converts one into the other:

1. **This runtime's own paper evidence.** As of 2026-07-25 it is **6 closed trades at −0.39R**.
   Gate 0 was corrected the same day: the "+2.36R over 114 trades" it had been ticked with was
   the **imported predecessor's** history, not this runtime's. There is currently no evidence
   this system trades profitably — not evidence that it does not, simply too small a sample over
   too few days (the digest still says `INSUFFICIENT_SAMPLE`).
2. **≥ 3 clean canary orders** (currently 0) — the plumbing has never been exercised against the
   real venue from this codebase.
3. A registered budget, the `live_trading` grant, the order key, and the confirmation phrase — all
   operator steps, none of them present today.

Building the executing leg before (1) and (2) means the autonomous path becomes reachable before
anyone has evidence it should be. The recommendation in this record is therefore explicit:
**do not build LP5.3's remainder until this runtime's own paper record justifies it.**

## Proposed sequencing, when it is built

1. `execute_live_entry` + `execute_live_exit` in a new `crypto/live_leg.py`, with the adapter
   injected — so every branch (not RECONCILED, bracket failure → naked close, cancel-on-close,
   realized PnL from fills) is testable with a fake adapter and **zero network**.
2. Extend the guard scope to include live outcomes (above).
3. Only then the cycle routing, which is the line that removes
   `test_no_autonomous_entry_point_reaches_the_live_order_path`. That test's removal should be
   its own reviewable commit, because it is the moment the safety posture changes.
4. First supervised runs with the smallest configured caps, watched live.
