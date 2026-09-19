# Crypto Live Execution v0.1

**Status:** Partially implemented — LP1, LP2, LP3, LP6, **LP4**, and all of LP5 except **cycle
routing** are shipped, including LP5.3's entry decision *and* its executing leg. **An order path
exists** (since 2026-07-25) and so does the leg that opens, protects and closes a position; what
does not exist is any autonomous **caller** for it. See the table below — this replaced "no order
path exists", which had become false, and the difference matters more than any other line here.
**Owner:** Thomas
**Authority:** None. The canonical Governance Policy (`governance/GOVERNANCE_POLICY.yaml`)
owns every rule this describes. The governance decisions this work needed are recorded
separately in `LIVE_EXECUTION_GOVERNANCE_V0.1.md` and are **now implemented** — that document's
own step table is the authority on which steps landed and which remain.

Ports the live-execution stack of the frozen `crypto_AI_System` project into this runtime.
That stack was not merely designed there — it was built and verified against the real venue
(a signed testnet order FILLED and reconciled 2026-07-15; one real mainnet canary order FILLED
and reconciled with zero mismatches 2026-07-16; the autonomous L1–L6 path implemented and
never enabled). `CRYPTO_PIPELINE_V0.1.md` deliberately excluded all of it from the C-phase
port, then the source repo was frozen. This document covers bringing it across.

## What is here, and what is emphatically not

| Leg | Source | Ported | Can it send an order? |
|---|---|---|---|
| LP1 live account read | `live_canary_preparation.LiveReadOnlyProbe` | yes | **no method exists** |
| LP2 P&L ledger + loss breaker | `execution/live_pnl_ledger.py` (L1) | yes | no |
| LP3 order intent + final guard | `execution/live_order_final_guard.py` (L2) | yes | no — it only refuses |
| LP6 canary promotion evidence | `execution/live_promotion.py` (L5 gate) | yes — the gate and the registry writer were removed 2026-09-15 (PR1r); the verified reader and the history board remain | no |
| LP4 order adapter | `execution/live_canary_adapter.py` | **yes** (2026-07-25) | **YES** — `live_execution.py`, behind `MVP_LIVE_TRADING=real` + the order key + a guard PASS |
| LP5 position kernel + routing | `execution/live_position_kernel.py` (L5/L3/L6) | **almost** — 5.1 state/reconciliation, 5.2 sizing, 5.3 the entry decision **and the executing leg**, 5.4 the outcome bridge | the executing leg can, with an **injected** adapter — but nothing autonomous may import it (tripwire test), so **cycle routing is the only piece left** |

The honest summary changed on 2026-07-25 and is worth stating without softening: **an order path
now exists.** Every other module either **reads** or **refuses**, and the one that can send
requires, simultaneously, `MVP_LIVE_TRADING=real`, the order-capable API key, a registered
budget, the autonomous confirmation phrase (or, for a slippage-probe entry only, the separate
canary phrase), both kill switches clear, and a guard PASS.

**The second half of that summary expired on 2026-07-28** and is corrected rather than deleted:
it used to end "and even then it is only reached from the deliberate
`scripts/place_canary_order.py`, one canary at a time." Cycle routing shipped that day, so a
scheduled run also reaches it — through exactly one module, `crypto/live_route.py`, pinned by
`test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`. "Nothing reaches it on
its own" is no longer the property; "exactly one thing may" is.

LP5.3 built the **decision** (`live_entry.plan_live_entry` — no adapter, imports none) and then
the **executing leg** (`live_leg.execute_live_entry` / `execute_live_exit`). The leg takes its
adapter as an argument rather than selecting one, so it cannot reach the venue on its own, and
the tripwire test now refuses to let the cycle, the scheduler, the pipeline or the operator loop
import it. **What is left is the cycle routing** — the line that gives the leg an autonomous
caller, and therefore the moment the safety posture changes. It is its own decision.

## Effect-tier mapping

| Behavior | Effect | Expression here |
|---|---|---|
| Account balance / positions / realized P&L | External read | `INTERNAL_READ` · ALLOW behind its own `binance_futures_account` grant; failure **degrades** (`ACCOUNT_DATA_DEGRADED`), never blocks — the R3/`MARKET_DATA_DEGRADED` precedent |
| Realized live P&L ledger + daily-loss breaker | Internal state + validation | Records behind the live-trading switch; the breaker is a pure read every caller can make ungated |
| Order intent construction, idempotency, final guard | Internal compute | Pure functions. No gate, because computing a refusal is not a capability |
| Canary promotion evidence (history) | Internal read | Nothing appends since 2026-09-15 (PR1r removed the canary door and its registry writer); the frozen registry's reads stay ungated and verified |
| **Live order submission** | **External + financial** | **Implemented** (LP4, 2026-07-25) under the decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`: `FINANCIAL_APPROVED_TRADING_USE` at P5, the `execution.live_trader` role (**candidate, non-routable** — activating it is a separate `ROLE_GOVERNANCE` approval), a registered `live_trading_budget.v0.1`, and the `p5_policy_gate`. Reached from `crypto/live_route.py` (the autonomous leg) and `scripts/run_slippage_probe.py --fire`; it was reached only from `scripts/place_canary_order.py` when this row was written, and that door was removed 2026-09-15. `financial_executor_enabled` stays `false` |
| **Live entry decision** (LP5.3) | Internal compute | Pure functions — `live_entry.plan_live_entry` decides and refuses; it holds no adapter, so deciding is not a capability |
| **Live executing leg** (LP5.3) | **External + financial**, when given an adapter | `live_leg.execute_live_entry` / `execute_live_exit`. The adapter is **injected**, never selected, so the module cannot reach a venue on its own; it also refuses without a governance record. No autonomous entry point may import it (`test_no_autonomous_entry_point_reaches_the_live_order_path`), and the readiness board reports that as the `autonomous_routing_wired` row |
| **Venue trading rules** (`exchangeInfo`) | External read | `INTERNAL_READ` · ALLOW on the existing `binance_futures` market-data grant; failure **degrades** (`LIVE_FILTERS_DEGRADED`) and sizing then refuses |

## One env var is the whole switch

**Changed 2026-07-28 (Thomas).** This section described a per-machine `live_trading` grant,
minted by `scripts/activate_safety_flag.py`, that every live-side capability shared. That grant
is gone. The switch is now the environment opt-in alone:

```
MVP_LIVE_TRADING=real
```

Why it was removed, recorded so a future reader restoring the grant knows what they are undoing:
the grant was TTL-capped at 30 days on a system meant to run unattended for months, and — the
sharper reason — a grant that expired while a position was **open** shut the CLOSE path too.
`evaluate_live_close_guard` exempts a reduceOnly close from the loss breaker, the daily count,
the exposure cap, the promotion gate *(removed 2026-09-15, PR1r)* and both kill switches precisely
so a halt cannot trap a position; expiry walked around all of it.

What was given up: a second factor, an expiry, and an audited per-machine record of scope and
authority level. What was **not** given up: revocation. The operator console `kill` is
file-based, lands on a running service at its next guard, and is exempt on the close path — the
one thing grant expiry could never do.

The provider id and the flag pair survive the removal. `assert_authorization` still re-checks
them at every egress (re-reading the env var in place of the record), and each capable class
still declares them, so the capability still cannot be half-enabled — `network_access` to reach
the venue and `filesystem_write` to record what happened, never one without the other.

The consequences are deliberate:

* It cannot be half-enabled. Orders reaching the venue while the P&L ledger silently fails to
  record them is the exact failure mode a split switch would allow. The live selectors — order
  adapter, P&L ledger, position book, daily counter, bracket-failure breaker, and the entry marks
  (PR2a: the last bar each context sent on and the stop-loss cooldowns; PR2b-2: the symbols an entry
  has taken and not yet given back) — read the same variable,
  and a test pins the exact set of modules that select on it. *(The canary registry was one more
  until its writer went with the canary door on 2026-09-15.)*
* **It does not expire, and nothing revokes it but the operator.** This is the reversal, stated
  plainly rather than buried: live capability now persists by forgetfulness, which is what the
  30-day TTL existed to prevent. The mitigation is the `kill` verb, not the gate.
* Clearing the variable is **not** a mid-flight revocation. A running process keeps its
  environment; the egress re-check catches an authorization built earlier in the same process,
  but stopping a live scheduler means the runtime `kill` or a restart.
* Nothing else may use this weaker door without a decision. `select_env_gated` is a separate
  function from `select_gated` — not a flag on it — so moving another capability onto it takes
  a deliberate edit at the call site, and
  `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named` fails if one does.
  It has fired once as designed: the candle archive joined this door on **2026-08-04** (Thomas),
  and is the only non-live-trading capability on it. That one is read-only public candles with
  no key that feed nothing, so it does not widen this contract's blast radius — see
  `select_candle_archive_collector` for the reasoning and for what was given up.

The account read (LP1) deliberately keeps its **own** grant, `binance_futures_account`, and
**kept it through the 2026-07-28 change** — reading balances needs a key with a wider blast
radius than public market data, and it must be scoped, expired, and revocable independently of
the ability to trade. So the readiness board's `market_data_visibility` row still checks a grant
while `live_trading_opt_in` no longer does. That asymmetry is the decision, not drift.

## The rules carried over verbatim, and why

**Zero means "not configured", never "unlimited".** Every cap (per-order notional, daily order
count, open exposure, daily loss) defaults to 0, and 0 blocks. A missing risk limit is the most
dangerous state a trading system can be in, so it must read as halted.

**One source for the caps: the registered budget.** Since step 6b the caps come from the
self-hashed `live_trading_budget.v0.1` record (`scripts/register_live_trading_budget.py`), read
by `resolve_live_order_limits`. The `MVP_LIVE_MAX_*` env vars no longer authorize anything — a
missing, tampered or invalid budget — or one registered before 2026-09-15 that is outside the
validity window it carries — yields the blocking defaults above, so there is no cap an operator
can set outside the registered record. A budget registered since carries no window and stands
until it is re-registered (PR1r). Only three things stay env, because a phrase proving intent and
a halt are operator state rather than registered caps: `MVP_LIVE_CONFIRMATION` (autonomous
entries and every close), `MVP_LIVE_CANARY_CONFIRMATION` (slippage-probe entries only),
`MVP_LIVE_MANUAL_KILL_SWITCH`.
Both guards **require** their `limits` argument (no `from_env()` fallback), so the question
"which numbers was this order judged against?" has exactly one answer.

**An unconfigured loss limit counts as breached.** `daily_loss_limit_breached(None)` and `(0)`
both return `True`. This is the single most important line in `live_pnl.py`.

**A cap above the absolute ceiling is refused, not clamped.** 500 USDT is the hard ceiling a
configured cap can never exceed (200 until 2026-08-08; raised on Thomas's instruction once the
live path had earned its 4 clean canaries and 2 completed round trips). Silently resizing an
order would desync its size from the decision that approved it. The ceiling is the most a
*registered budget* may declare, not a size and not a cap any order is judged against — raising
it widens nothing until an operator registers a budget carrying the larger number.

**A missing notional is never back-filled from the cap.** The cap is a ceiling, not a size.

**Guards accumulate; they never short-circuit.** The operator sees every reason at once.

**Damaged evidence is no evidence.** Both the P&L history and the canary registry are verified
reads — self-hash plus duplicate-id detection. A tampered or unparseable row raises rather than
resolving. *(For the promotion gate it also counted as **zero** clean orders, never as the last
good number, until that gate was removed on 2026-09-15; the registry is frozen history now.)*
A non-numeric P&L amount raises too: reading it as zero would understate a loss and could clear
a breaker that should be tripped.

## Two decisions worth stating plainly

**The reduceOnly close path is exempt** from the loss breaker, the caps, the daily count, and
both kill switches (Thomas, 2026-07-23). A halt that traps you in a losing position is more
dangerous than the halt was meant to prevent. What survives is the structural
boundary — the grant, the confirmation phrase, and `reduce_only` itself — so that path can only
ever shrink a position, never open one.

**`kill_blocks: external_execution` finally has a door.** The governance vocabulary has listed
it since R4 with nothing bound to it. A PAUSED or KILLED runtime now blocks a live entry
(`live_order.evaluate_live_order_guard`), while a close stays permitted per the above.

## Reading the state

```
python -m runtime.mvp_runtime.crypto.account            # real balance, positions, P&L
python -m runtime.mvp_runtime.crypto.dashboard --account  # the pipeline board plus the account
python -m runtime.mvp_runtime.crypto.live_readiness     # every gate between here and a live order
```

`live_readiness` asks each gate directly rather than reasoning about them from documentation,
so its answer cannot drift from what the code enforces. It exits 0 only when every check
passes, and it **cannot report READY while no order path exists** — a row of green ticks that
implied otherwise would be the most dangerous output this repository could produce.

**Whether a live entry can open is not READY** (crypto PR5a). READY and the exit code answer for
the process that runs the board, and a console built without the live-trading environment is never
READY. The structured view's `live_entry_possible` answers for the machine: the three-valued AND of
eleven named components — installed, stage, gate, runtime control, armed count, venue, risk, account,
whether the pipeline is still firing, market data, reconciliation. Each is true; false where an entry
door refuses on it; or null where the reading process cannot see it. `readiness.blocking` and
`readiness.unknown` name each false and null one with its reason. A process without the environment
reads the trading process's own records, dated: its last fire's cycle records (the gate and its two
switches, data health, account reads, legs refused whole, incidents, the live allowance) and its
account snapshot. Two judgements are coarser than a door, and say so: a refusal the last fire
recorded per context counts when half or more of its contexts were refused (the pipeline's stall
rule), and no fire within three of the pipeline schedule's intervals — or no enabled schedule — is
false (`trading_cycle_recent`), because nothing runs to enter.
The text board opens and closes with that answer (`LIVE ENTRY POSSIBLE: YES / NO / UNKNOWN`,
PR5b; the last line is one line however long) and prints this process's own verdict as
`THIS PROCESS: READY / NOT READY`. A NO decided only by the stall rule says a minority of contexts
may still enter. Where the process cannot see the live-trading environment, its env rows read `n/a`
rather than FAIL unless a fresh record of the trading process says the gate was closed, and so does
the loss breaker's NO DATA SOURCE where the process reads no account.

## The venue contract sentinel (crypto PR4a/4b, Thomas decisions 43-46)

Every check before an order used to point at this runtime's own model of the venue. On 2026-08-02
they all passed while the venue refused both protective stops, because conditional order types had
moved to the Algo API — a fact that lives only at the venue. `crypto/venue_contract.py` asks the
venue itself.

- **When:** the pipeline fire (the risk lane, which holds the venue keys) asks after its cycles and
  after the account refresh — about hourly (every 55 minutes, so every fourth fire), and on every fire
  while the decided record is a FAIL — only while live trading is opted in and a valid budget names
  the symbols. Its line joins the fire's status line.
- **What it may use** (decision 43): a public GET, a signed GET, and `POST /fapi/v1/order/test`,
  which creates no order. No order or cancel verb is imported (pinned), and the literal validator path
  is pinned. Every client id it sends starts `TAI_VC_`. Its calls go to the raw adapter, not to the
  one the API breaker records.
- **It backs off:** the first answer that says the venue could not be asked (the breaker's own test:
  a rate limit or ban, a 5xx, a transport failure, a key or clock refusal) stops the run, and a fire
  whose market data was already rate limited is not run. Each call starts only with its full 4 s
  timeout inside a 30 s budget, beyond what the last three judged reads keep back, so a venue that
  answers within its timeouts always reaches a decision (the timeout bounds each socket operation;
  DNS is not bounded by it).
- **Judged checks** (each expectation measured at this venue), all of which must pass:
  - the traded symbols' `exchangeInfo` listing (per-symbol problems are recorded, so a door can tell
    one symbol's break from the venue's);
  - the 2026-08-03 diagnostic stop, sent to `/order/test` in its frozen pre-migration shape, must
    still be refused with -4120 (an answer that means "could not ask" is never a verdict);
  - one-way position mode (`GET /fapi/v1/positionSide/dual`);
  - configured leverage at most the backtests' 5x (decision 45), read from the account snapshot this
    lane refreshes every 15 minutes, and judged only on a snapshot at most 45 minutes old;
  - the entry test left no order: the entry test's own id, asked of the order API afterwards, names
    nothing (a filled order rests nowhere, so only its id can find it). The id asked for is one that
    could name an order (PR4b-2): the first symbol whose entry test the venue accepted, else the first
    left unanswered (no answer of the venue's, or a 5xx). A request skipped, refused by the venue or
    refused by the builder before it left created nothing; none of those is asked for;
  - nothing the sentinel sent is resting.
- **Observed checks** are recorded and never judged until the host's answers are known: the
  runtime's MARKET entry and a reduce-only take-profit LIMIT twice the `PERCENT_PRICE` band away
  at `/order/test`, and the answer to an algo id no order carries.
- **What it cannot show** is written into every record (`not_verified`): an algo order's
  placement (no validator exists; the signed testnet cycle is that evidence), the codes only a
  real order or cancel produces, and account state, which `/order/test` does not judge.
- **Records:** `venue_contract.json` holds the last decided verification (PASS or FAIL,
  self-hashed, schema `venue_contract_verification.v0.1`); `venue_contract_refresh.json` the last
  attempt. A run that could not decide moves only the attempt; a violation is written as FAIL at
  once. A PASS is usable for six hours, only under this code's `CONTRACT_VERSION`, and only for the
  symbols it covered (decision 44).
- **Not stage evidence** (decision 3).
- **Enforced (PR4b, decision 46):** a mainnet autonomous entry and a probe are decided on a usable
  PASS for their symbol — this code's `CONTRACT_VERSION`, at most six hours old at the moment of the
  decision (`clock`), dated no more than five minutes ahead, naming the symbol. The rule is one pure
  function (`venue_contract.entry_refusal`) that the autonomous decision's door
  (`venue_contract_verified`), the probe's gate and the board's `venue_contract` row all call; which
  symbols a verification covers is one more (`venue_contract.covers`), shared by the judge, the row
  and the refresh cadence. Each way it can refuse has its own code, so the refusal says what to do:

  | Code | Means | Operator |
  |---|---|---|
  | `LIVE_ENTRY_VENUE_CONTRACT_MISSING` | nothing decided yet | wait for the pipeline fire, or `--run` |
  | `LIVE_ENTRY_VENUE_CONTRACT_UNREADABLE` | the record fails its hash, schema or parse | find out who changed it |
  | `LIVE_ENTRY_VENUE_CONTRACT_VERSION` | verified under another contract version | the next fire re-verifies (asked sooner, like a FAIL) |
  | `LIVE_ENTRY_VENUE_CONTRACT_NOT_PASS` | the venue contradicted an assumption | read `--show`; re-asked every fire |
  | `LIVE_ENTRY_VENUE_CONTRACT_STALE` | older than six hours at the decision | the sentinel has not been answered |
  | `LIVE_ENTRY_VENUE_CONTRACT_SYMBOL_NOT_COVERED` | the PASS did not name this symbol | the next fire verifies the budget's symbols (asked sooner) |

  The autonomous leg reads it once beside the breakers and again in the gate's re-read; the gate
  judges the re-read unless the first read already refused, so an entry needs both reads usable. The
  probe refuses early (`PROBE_VENUE_CONTRACT`) — a record on this machine, so before any signed call
  or API-breaker count; after the cell is chosen, so a probe in flight is named first — and its gate
  judges the re-read. The pre-order snapshot
  seals which verification backed the order (`facts.venue_contract`: the judged fields and the
  record's hash; the per-check answers stay only in the record, until the next decided run replaces
  it). "Asked sooner" is the cadence a FAIL already had: while the decided record refuses entries the
  next ask can let through (a FAIL, another contract version, a budget symbol it does not name), every
  fire asks; a run that decides nothing keeps the hour (decision 44). A FAIL anywhere blocks
  every symbol; narrowing that to the symbols a FAIL names is a relaxation for Thomas to decide, not a
  default. **Closing, protecting and settling never read it**, and neither does the testnet door: the
  signed testnet cycle is itself a venue answer, with real orders.
- **The operator is told on the edge (PR4b-2).** The doors refuse on the record quietly, so after
  its own ask the pipeline fire compares what the doors would answer about the record (usable, or
  the refusal's code) with what the operator was last told, and sends one message when it moved:
  a PASS turning FAIL (with the failed checks), a FAIL that now names other checks, a PASS going
  stale, a damaged record, a FAIL recovering, and a first report when there is no mark or it cannot
  be read. Nothing while it holds. The told reading (`venue_contract_notice.json`) moves only once
  the operator channel took the message. One it did not take is kept on the mark as undelivered and
  said with the next message, even when the reading has returned by then (the breaker watch's missed
  transitions). A message sent but not marked is sent again at every fire until the mark can be
  written: loud, never silent. A transport failure is on the fire's status line and never stops the
  fire; each socket operation of the send is bounded by the channel's 30 s timeout, after every
  trading step of the fire. The reading is the record's alone: a budget symbol the record does not
  name is on the board and asked sooner, and is not a notice.
- **Not stopped by** the PAPER stage or the manual kill switch env — it places nothing; the scheduler's
  own kill/pause stops the fire it rides. Revoking it is unsetting `MVP_LIVE_TRADING` and restarting.

## Operator go-live checklist

Real money. Work top to bottom on one machine. Every step is Thomas's; **Claude does not run
these, does not handle real keys, and does not enable live trading.** Steps 1–3 are already
satisfied or are blocked on work that does not exist yet, so this is a map, not a runbook.

**Gate 0 — earn confidence (before any live money)**
- [ ] Paper trading **by this runtime** shows positive expectancy over a sustained window.
      Check with `python -m runtime.mvp_runtime.crypto.dashboard`.

      **This is an operator step again, as written. Amended 2026-08-03.** #409 wired the
      computed `live_candidate_eligible` into `live_entry` as an automatic refusal, and #413
      added a signed, expiring record to override it. Both are removed. The checklist item is
      unchanged — a person still has to be satisfied on this before real money — but the
      runtime no longer refuses on it.

      **Why: the automatic form could not be satisfied.** Measured 2026-08-03 on this machine,
      `routable_strategy_ids` is whichever batch was promoted last (5 of 94 entries; the other
      89 SUSPENDED), promotions land every 1–3 days, and the acknowledgement binds by exact set
      equality — so each promotion reset the sample and voided the signature in the same event.
      The sample stood at 2 against a required 20, roughly 32 days of a frozen pool away, on a
      pool that does not stay frozen. Its only reachable state was the override, which makes it
      a signature requirement rather than an evidence gate.

      Measurement, alternatives considered, and what replaces it (nothing pool-wide; the
      per-strategy lifecycle ladder already gates routing off its own record):
      `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md`.

      **Corrected 2026-07-25.** This box was previously ticked citing "2.36R over 114 closed
      trades". Those 114 are the **imported crypto_AI_System history**
      (`provenance: crypto_ai_system_import`, brought in by `scripts/import_crypto_history.py`),
      not trades this runtime made. On the same day this runtime's own record
      (`provenance: mvp_paper_kernel`) was **5 closed trades at −0.53R**, and the dashboard's
      blended headline had hidden that — it also flipped the recommendation from
      `DROP_CANDIDATE_PROFILE` to `CREATE_CANDIDATE_PROFILE_DRAFT`. The dashboard now reports the
      two populations on separate lines so the gate cannot be read that way again.

      A go-live gate must be earned by the code that will trade. The predecessor's record is
      context, not evidence about this runtime. Two things make it unearned today: the sample is
      5 trades, and the window is days — the digest itself still says
      `weekly_trend: INSUFFICIENT_SAMPLE`.

      Worth knowing when this is re-judged: the paper kernel is structurally optimistic. Exits
      settle at the **modelled** stop/target price (a stop-out books exactly −1.00R), and there
      are **no fees, funding, or slippage** on the paper route. So paper expectancy is an upper
      bound on live expectancy, not an estimate of it.
- [ ] The active pool is populated with strategies you trust. The former symbol-starved finding
      is resolved: a crypto schedule with an empty request now fans out over every
      ``(symbol, timeframe)`` the pool routes on — plus every context that holds an open paper
      position, so a demoted strategy's position still settles — via
      ``cycle.run_pool_cycle`` (a named ``SYMBOL [TIMEFRAME]`` request still pins one context as
      an operator override). Multi-symbol strategies are covered too: ``route_entries`` now
      matches on the whole ``symbol_scope`` (not just ``symbol_scope[0]``) and the plan books
      under the traded symbol, so a strategy scoped to several symbols opens an independent
      position in each of its symbols' books. (Caveat: the factory only mints single-symbol
      specs and backtests on one symbol; a hand-authored/imported multi-symbol strategy trades
      symbols its evidence did not cover — an operator judgment at authoring time.)

**Gate 1 — the code must exist**
- [x] **LP4 (order adapter) merged** 2026-07-25 — `live_execution.py`, real signed transport,
      conditional order types, and the lockstep governance flip
      (`financial_transaction_execution_implemented: true` + `ORDER_PATH_IMPLEMENTED = True`).
      The governance decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md` are now implemented except
      where that document's own step table says otherwise.
- [~] **LP5** — state + reconciliation (5.1), sizing (5.2), the entry decision **and the
      executing leg** (5.3), and the outcome bridge (5.4) are merged. Only **cycle routing** is
      not: the executing leg takes an injected adapter and no autonomous entry point may import
      it (a test enforces that, and the readiness board reports it as a computed row). Wiring a
      caller is what an autonomous live order would need, and it is a separate explicit decision,
      not a remaining chore.

**Gate 2 — configure the boundary (conservative first)**

> **Ordering corrected 2026-07-26.** This gate used to be numbered *after* the canary gate, which
> could not be followed: a canary is exempt from the **promotion gate and nothing else**, so every
> item below has to be in place before the first canary can be placed at all. Configure, then
> canary. *(2026-09-15: the canary gate and the promotion gate are both gone — see Gate 3. The
> items below are what the autonomous leg and the slippage probe need.)*

- [ ] `git pull`. Before 2026-07-26 `resolve_live_order_limits` dropped `canary_confirmation`, so
      an older checkout refuses **every** canary-mode order (today: every slippage-probe entry)
      with "canary confirmation phrase not present".
- [ ] **Activate the Core on this machine** (`CLAUDE.md` → "Core activation"). Since the live order
      path builds a P5 PermissionDecision bound to an active Core, a machine without one refuses
      with `CORE_NOT_ACTIVATED` *before* the order — governance is prepared before money moves.
- [ ] Configure the **read-only account feed**: `MVP_ACCOUNT_FEED=binance_futures_account` plus
      `BINANCE_ACCOUNT_API_KEY` / `BINANCE_ACCOUNT_API_SECRET`. The autonomous leg and the probe
      refuse an entry without it (the canary script did too, until its removal on 2026-09-15) —
      open exposure would be unknown, and the exposure cap cannot be honored on a guess.
- [ ] Configure the **read-only market-data feed**: `MVP_MARKET_DATA=binance_futures` plus a
      `network_access` grant for the `binance_futures` provider
      (`scripts/activate_safety_flag.py`). Public endpoints, no API key — the grant is for
      crossing the network, not for a secret. Without this feed the mock collector is selected,
      and its price is a hash of the symbol rather than a market: the cycle has nothing real to
      judge, and the slippage probe's price read (`market_data.read_reference_price`) refuses
      (`PROBE_PRICE_UNREADABLE`) instead of pricing a real order off a fabricated number. The
      readiness board reports this as `market_data_visibility`. *(Until 2026-09-15 this item was
      a canary precondition: the canary script checked the `--notional` you declared against the
      same price read and refused with `ORDER_NOTIONAL_PRICE_UNKNOWN`. The check went with the
      script.)*
- [ ] Create a **separate** order-capable live API key: enable Futures, **disable withdrawals
      and internal transfer**, IP-whitelist it. Keep it distinct from the read-only account key.
      `MVP_LIVE_ORDER_API_KEY` / `MVP_LIVE_ORDER_API_SECRET`.
- [ ] Register the budget — the caps come from the record, never from env. Approved starting
      values (Thomas, 2026-07-23): 60 USDT per order, 2 orders per day, 120 USDT open exposure,
      20 USDT daily loss, against a 200 USDT absolute ceiling. **These are still the values a
      first bring-up registers**, and they remain this script's defaults; the *hard ceiling* a
      budget may declare rose to 500 USDT on 2026-08-08, which raises the lid, not the caps.

      ```
      python -m scripts.register_live_trading_budget --registered-by thomas \
          --max-order-notional 60 --max-daily-order-count 2 \
          --max-open-notional 120 --daily-loss-limit 20 --absolute-max-notional 200
      ```

      A re-run replaces the whole record rather than the flags you pass, so name every cap
      **and** `--symbols` on each re-registration: the script's defaults (the caps above,
      BTCUSDT only) are lower than what a machine past bring-up has registered, and an omitted
      flag lowers a cap or narrows the allowlist with no warning. Read the result back off the
      readiness board rather than assuming it landed.

      The record does not expire: a budget registered since 2026-09-15 carries no validity
      window and stands until it is re-registered, or until `live_trading_budget.json` is
      deleted. `--valid-days` and `--min-clean-canary-orders` were removed that day (PR1r);
      passing either exits 2 and writes nothing. A budget registered before still carries its
      window and is still held to it, and it still carries `caps.min_clean_canary_orders`, which
      nothing reads. **Re-register only once the image that reads it is deployed:** an older
      image reads a windowless record as schema-invalid and refuses every entry (closes keep
      working).
- [ ] Set the confirmation phrases. They are deliberately distinct, so pasting the wrong one
      authorizes nothing — and they are **not** symmetric. `MVP_LIVE_CONFIRMATION` authorizes
      autonomous entries **and every close**, the slippage probe's exits included
      (`evaluate_live_close_guard` compares it). `MVP_LIVE_CANARY_CONFIRMATION` authorizes
      slippage-probe entries only. So the close phrase is also the autonomous-entry phrase, and
      since 2026-09-15 no canary floor stands behind it — what does is the execution stage
      (PR1b: the guard refuses below `LIVE_AUTONOMOUS`, the probe included) and, behind it, a
      probe-only session keeping every pool entry OBSERVATION-tier (`live_armed_strategies` = 0).
      *(Until 2026-09-15 this item read "`MVP_LIVE_CANARY_CONFIRMATION` for canaries,
      `MVP_LIVE_CONFIRMATION` for autonomous trading. A canary needs only the first." — true for a
      canary, which only opened, and never true for the probe, whose exits need the second.)*
- [ ] Set `MVP_LIVE_TRADING=real`. **This is now the entire gate** — there is no grant to mint
      since 2026-07-28, so this one line selects every real live component at once. Confirm it
      on the board (`live_trading_opt_in` PASS) before continuing rather than assuming.

**Gate 3 — promotion evidence (removed 2026-09-15)**

Retired, not skipped. This gate asked for 3 clean canary orders, placed one at a time with
`scripts/place_canary_order.py`, before any autonomous run; the final guard refused an autonomous
entry until the canary registry held them. Canaries ended on 2026-07-29 (Thomas: no further
canaries, the evidence moves to real trades), and on 2026-09-15 Thomas removed the door, its
registry writer and the guard's clean-canary promotion gate together (PR1r). With the door went
its declared-notional check (`ORDER_NOTIONAL_UNDERSTATED`, `ORDER_NOTIONAL_PRICE_UNKNOWN`) and the
budget's `--min-clean-canary-orders` bar. The frozen registry is still verified and shown by
`python -m runtime.mvp_runtime.crypto.live_promotion`, and nothing counts it. **The execution
stage replaced this floor on 2026-09-16 (PR1b): a fresh machine reads `READ_ONLY` and every new
entry is refused until Thomas registers a rung** (`EXECUTION_STAGE_V0.1.md`). The steps this gate held are in git history and in
`docs/BUILD_HISTORY.md`.

**Gate 4 — verify the gate before any autonomous run**
- [ ] `docker exec thomas-scheduler python -m runtime.mvp_runtime.crypto.live_readiness` reports
      `THIS PROCESS: READY` and ends `LIVE ENTRY POSSIBLE: YES`. Either refusal names exactly what
      is missing (`blocked by` names the component and its reason); fix it rather than working
      around it.

**Gate 5 — first supervised cycles**
- [ ] Watch the first entries and closes live. Confirm each entry reconciles and each position
      closes on stop-loss, take-profit, or time.
- [ ] Watch the daily-loss breaker and the open-exposure cap behave.

**Standing controls — know these before you start**
- **Stop new entries and keep managing open positions:** the Trading Soft Halt —
  `console_cli halt_trading` or `/halt_trading` (Thomas decision 7, 2026-09-15). The runtime stays
  ACTIVE, so settlement, the protection re-check, the time exit and reconciliation keep running;
  only new entries (autonomous and probe) are refused, until `/resume`. From a PAUSED or
  KILLED runtime the operator's `/halt_trading` moves it straight to that state. **Policy 1.5.1
  grants the verb (2026-09-17); an image older than that policy refuses it by name.**
  **Two levels (Thomas decision 47, 2026-09-19):** `halt_trading` alone, or `halt_trading soft`, is
  the SOFT halt; `halt_trading hard` (`/halt_trading hard <reason>`, switch door `disable mode=hard`)
  is the HARD halt. Both keep the runtime ACTIVE and refuse new entries. HARD is the tighter one:
  tightening SOFT to HARD needs nothing, and only the authenticated operator (local console,
  Telegram) loosens HARD to SOFT; `/resume` clears either. `/kill` and `/pause` keep their meaning
  and carry the level, so a resume that does not re-arm (the assistant's approved runtime-only
  resume) comes back to the halt that was in effect. The level is a field beside `mode`
  (`halt_level`), recorded on every control event (`resulting_halt_level`) and recovered from the
  ledger when the state file is lost; a corrupt file still reads KILLED (decision 48), with a HARD
  halt under it. An image from before halt levels ignores the field and reads either level as the
  soft halt. `/status` shows a `halt:` line, and the readiness board names the component
  `runtime_control (SOFT_HALT)` or `(HARD_HALT)`, apart from `TRADING_DISARMED`, a disarm nobody
  named. The policy's comment on the grant still describes the soft halt alone: it is a byte of the
  fingerprinted policy, so it changes with the next policy bump Thomas applies.
- **Stop everything:** the operator console `kill` (or `pause`). It writes control state and lands
  on the running service at its next fire — but it does **not** leave closes running. Corrected
  2026-09-15 (execution-authority audit, verified): `kill_blocks` also carries
  `scheduler_execution` and `tool_write`, so the scheduler drops every `crypto_pipeline` fire and
  the paper step refuses before the live leg; settlement, the protection re-check, the time exit,
  reconciliation and the route/breaker watches all stop until `/resume`, and an open position is
  held only by the brackets resting at the venue. The close guard's exemption from "both kill
  switches" is real but reachable only while the runtime stays ACTIVE (the soft halt, the env switch
  below). This bullet said "closes remain permitted" until then.
- **Softer halt, next restart:** set `MVP_LIVE_MANUAL_KILL_SWITCH=true` and restart the
  scheduler. Refuses entries; the runtime stays ACTIVE, so closes and management continue. Reach
  for the soft halt above first — it lands on the *running* service, this one waits for a restart.
  History worth keeping, because this bullet was false for longer than anyone would guess: until
  2026-09-07 `docker-compose.yml` forwarded this variable to no service, so setting it in `.env`
  and restarting halted nothing on the scheduler the autonomous entry path runs on, and the
  compose comment next to it had claimed "both kill switches" the whole time. It was found while
  tracing an unrelated env-passthrough fault, not by anyone trying to use it — which is the
  argument for testing that a documented control is reachable, not just that it is implemented.
- **Do NOT clear `MVP_LIVE_TRADING` to halt.** It needs a restart *and* it shuts the close path,
  because `evaluate_live_close_guard` requires the opt-in — it would strand open positions.
- **Daily-loss breaker:** entries halt for the UTC day once realized live loss reaches the
  configured limit, and resume the next UTC day.

## Deliberately not ported

The x10 SDK, the streamlit dashboards, the source's backtesting UI, and the legacy
`live_guard`/`order_executor` review-only surfaces. The source's separate **signed testnet**
boundary is also not ported: this runtime's mock/paper path already covers pre-live rehearsal,
and a second venue with its own keys, hosts, phrases, and counters is more surface than it
earns. If testnet rehearsal is wanted later it is a deliberate addition, not an oversight.
