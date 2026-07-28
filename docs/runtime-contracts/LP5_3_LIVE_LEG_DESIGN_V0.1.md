# LP5.3 — The Executing Leg + Cycle Routing: Design Record v0.1

**Status:** IMPLEMENTED — the executing leg (`crypto/live_leg.py`, Thomas 2026-07-25) and the
**cycle routing** (`crypto/live_route.py`, 2026-07-28) are both built. An autonomous live order
is now structurally reachable, and what stands between the wiring and an order is the gate
rather than the absence of a caller: no grant, no phrase, no registered budget, no canary
evidence → no order, each on its own readiness row.
**Owner:** Thomas
**Authority:** None. `governance/GOVERNANCE_POLICY.yaml` owns every rule.

> **Read this first.** Every other increment in this stack could be described as plumbing. This
> one cannot. LP4 shipped an order path and `financial_transaction_execution_implemented` is
> `true`; LP5.1/5.2/5.4 shipped state, sizing and the outcome bridge; LP5.3's *decision* half
> (`live_entry.plan_live_entry`) shipped too. **The executing leg and the cycle routing are the
> piece that makes an autonomous live order structurally reachable**, and as of 2026-07-28 both
> have landed. Until then the only door was `scripts/place_canary_order.py`, one deliberate
> canary at a time, held there by a test
> (`test_no_autonomous_entry_point_reaches_the_live_order_path`) that failed loudly if any
> autonomous entry point imported the order path.
>
> **That test is gone, deliberately, and what replaced it is the thing to check next.** Its
> successors are `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module` and
> `test_the_chokepoint_is_the_only_runtime_module_that_imports_the_executing_leg`: the cycle may
> reach the venue, but only through `crypto/live_route.py`, so "which code can start a live
> order" keeps exactly one answer. Adding a second caller is the same size of decision as adding
> the first was.

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
3. [x] **The cycle routing.** Done 2026-07-28 (`crypto/live_route.py`), on Thomas's explicit
       instruction. The safety posture changed here, and the change is in one place: `cycle.py`
       runs a live leg through one module, which is inert without the `live_trading` grant.
       What landed with it:

       - `live_route.run_live_leg` — gate → reconcile → settle → protect → maybe open, with
         every runtime fact read once and shared by every door;
       - the **shared route**: `run_paper_update` now returns its own routing result and both
         the counterfactual shadow and the live leg consume it, so three consumers cannot
         disagree about what the pool said (it was evaluated twice before, identically, which
         is two chances to differ);
       - `live_leg.settle_venue_closed_position` + `read_bracket_legs` — the venue's own exit.
         Not in the original shape, and **required rather than optional**: see below;
       - the portfolio-level halt: `run_pool_cycle` stops the fan-out on a live incident and
         names the contexts it never visited, rather than filing one context's real-money
         uncertainty as a skipped row;
       - the readiness board's real open exposure (it already reads the account for the loss
         breaker, so the honest block-at-cap lifts on a machine that can see its own account)
         and `AUTONOMOUS_ROUTING_WIRED = True`, pinned to the import graph by a test.
4. [ ] First supervised runs with the smallest configured caps, watched live.

The preconditions above (this runtime's own paper record, ≥ 3 clean canaries, the operator
grants) were unchanged by steps 1 and 2: an executing leg with no autonomous caller places no
orders. They bind step 3 — and step 3 is now built, so they bind at **run** time rather than at
**build** time, which is the weaker of the two positions this record warned about. Where each
stood on 2026-07-28: canary evidence **met** (4/4 clean on Thomas's machine, against the 0 this
record was written with); operator grants **not** met (the `live_trading` grant expired
2026-07-27, and the confirmation phrase and account feed are unset); paper record **improved but
not met on its own terms** (60 closed trades at +0.08R, against the 6 at −0.39R recorded above;
the digest still says `INSUFFICIENT_SAMPLE`, so "positive expectancy over a sustained window" is
not yet earned). Step 4 is where that last one gets judged, and it is a Thomas decision.

## What the routing found (2026-07-28)

Two things the design did not anticipate, both worth carrying.

**1. The exit the cycle owes is protection, not timing.** This record sketched a live leg that
"decides the close", by analogy with the paper cycle. But paper decides closes because paper has
no venue holding a stop; live does. What the cycle can honestly do is (a) record the close the
venue already made, and (b) close a position whose bracket is positively gone — rule 2 applied
continuously rather than only at entry.

(a) turned out to be load-bearing rather than a nicety. The normal end of a live trade is the
resting bracket, which leaves the local book holding a position the venue no longer has. Without
settling that, the first **successful** trade strands its own book: reconciliation reports DRIFT
forever and every later entry on that symbol is refused — a protective mechanism working exactly
as designed, indistinguishable from a fault.

There is deliberately **no time-based exit**. A live position record carries no holding count and
no timeframe, so a `max_hold` rule here would invent state rather than read it; adding it is
LP5.1's record shape, not this increment's routing. Consequence, stated because it shows up in
the numbers: a live trade ends at its stop or its target where a paper trade of the same strategy
may also end on time, so the two R populations differ by exactly that.

**2. "The venue answered" and "the read failed" must not collapse into one branch.** Both look
like "the bracket is not there"; only the first is evidence. `read_bracket_legs` therefore
returns three states rather than two — PROTECTED / UNPROTECTED / PROTECTION_UNKNOWN — and only
UNPROTECTED may send a close. This is the same boundary `_close_naked_position` already drew
between exposure the venue *reported* and exposure merely suspected. Having to draw it again one
level up suggests it is the stack's real invariant rather than one function's local rule.
