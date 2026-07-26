# LP5.3 — The Executing Leg + Cycle Routing: Design Record v0.1

**Status:** PARTIALLY IMPLEMENTED — **the executing leg is built** (`crypto/live_leg.py`, Thomas
2026-07-25). The **cycle routing is not**, and remains the line that makes an autonomous live
order reachable. Nothing here enables trading: the leg takes an injected adapter, and no
autonomous entry point may import it (the tripwire below now covers `live_leg`).
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

## What implementation found that the design did not (2026-07-25)

Both were discovered by a test failing against the design as written, and both changed the code
rather than the test.

**1. "Not RECONCILED" is not the same as "nothing happened", and the gap is the dangerous case.**
The design says an unconfirmed entry should *record, open nothing, stop*. But LP4 reconciles a
**partial fill as MISMATCH** (it compares `executedQty` against the intent), and a fill whose
price will not parse fails too — while in both the venue reports real filled quantity. That is an
open, **unprotected** position, and stopping there would leave it that way, which is precisely
what rule 2 forbids. So the leg now closes any exposure **the venue actually reported**, even
when the entry as a whole is refused.

The boundary is drawn at reported-vs-guessed: an `UNRECONCILABLE` result (no venue answer at all)
reports nothing, so nothing is assumed and nothing is sent. Firing a close against an unknown
account state would be acting on a guess; the next cycle's reconciliation sees the drift and
refuses new entries on that symbol, which is the honest handling of "we do not know".

**2. A protective order rests as `NEW`; `submit_and_reconcile` cannot confirm one.** That
function reconciles against `status == FILLED`, which is right for an entry or a close and wrong
for a bracket — reusing it would report every healthy stop as a MISMATCH. Bracket placement
therefore has its own confirmation (`place_bracket_leg`), and the inert `DryRunOrderAdapter` was
corrected to echo `NEW` for conditional types, because a dry run that "confirmed" a protective
order in a state the venue never reports is exactly the confidence a dry run must not manufacture.

**A related honesty note:** the dry-run adapter reports no fill *price*, so a dry run cannot book
a position at all. That was left as-is rather than "fixed" by inventing a price — a fabricated
entry price entering the book is the same class of mistake as a mock inventing a lot step.

## Known limitation carried into the implementation

Realized P&L is computed from the venue's actual fill figures and is therefore **gross of fees
and funding**. The fee-inclusive figure is the account snapshot's `realized_windows`, but that is
per-window rather than per-position, so attributing it to one trade is unsound while more than
one position can be open. Every outcome records `fees_included: false`, `pnl_source`, and both
legs' quote amounts so a later reconciliation can correct it. **The direction of the error is
named because it matters:** gross P&L understates a loss by roughly the taker fee on both legs,
which moves the daily-loss breaker the *permissive* way.

## Sequencing — where it stands

1. [x] `execute_live_entry` + `execute_live_exit` in `crypto/live_leg.py`, adapter **injected**,
       so every branch (not RECONCILED, partial-fill exposure, bracket failure → naked close,
       cancel-on-close, realized P&L from fills, ledger-before-book-clear) is exercised with a
       fake adapter and **zero network**. Done 2026-07-25.
2. [x] The guard scope extended to live outcomes. Live results are this runtime's own trading and
       the only kind that costs real money, and they live in their own store — so the paper
       provenance split never saw them and the breaker would have ignored every live loss. Routed
       through LP5.4's bridge rather than concatenated raw, because `guards._closed_rows` reads a
       missing `result_R` as `0.0` (a *breakeven*), so an R-less live loss would have **shortened**
       a loss streak. An unreadable or tampered live history fails the guard closed, exactly like
       an unreadable paper one.
3. [ ] **The cycle routing.** Still unbuilt, still the moment the safety posture changes. The
       tripwire now covers `live_leg` as well, so wiring an autonomous entry point to the
       executing leg fails a test rather than happening quietly — and relaxing that test should be
       its own reviewable commit.
4. [ ] First supervised runs with the smallest configured caps, watched live.

The preconditions above (this runtime's own paper record, ≥ 3 clean canaries, the operator
grants) are unchanged by steps 1 and 2: an executing leg with no autonomous caller places no
orders. They bind step 3.
