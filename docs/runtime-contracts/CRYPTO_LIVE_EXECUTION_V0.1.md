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
| LP6 canary promotion evidence | `execution/live_promotion.py` (L5 gate) | yes | no |
| LP4 order adapter | `execution/live_canary_adapter.py` | **yes** (2026-07-25) | **YES** — `live_execution.py`, behind `MVP_LIVE_TRADING=real` + the order key + a guard PASS |
| LP5 position kernel + routing | `execution/live_position_kernel.py` (L5/L3/L6) | **almost** — 5.1 state/reconciliation, 5.2 sizing, 5.3 the entry decision **and the executing leg**, 5.4 the outcome bridge | the executing leg can, with an **injected** adapter — but nothing autonomous may import it (tripwire test), so **cycle routing is the only piece left** |

The honest summary changed on 2026-07-25 and is worth stating without softening: **an order path
now exists.** Every other module either **reads** or **refuses**, and the one that can send
requires, simultaneously, `MVP_LIVE_TRADING=real`, the order-capable API key, a registered
budget, the autonomous confirmation phrase (or the separate canary phrase), both kill switches
clear, and a guard PASS.

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
| Canary promotion evidence | Internal record creation | Append behind the live-trading switch; reads ungated and verified |
| **Live order submission** | **External + financial** | **Implemented** (LP4, 2026-07-25) under the decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`: `FINANCIAL_APPROVED_TRADING_USE` at P5, the `execution.live_trader` role (**candidate, non-routable** — activating it is a separate `ROLE_GOVERNANCE` approval), a registered `live_trading_budget.v0.1`, and the `p5_policy_gate`. Reached only from `scripts/place_canary_order.py`; `financial_executor_enabled` stays `false` |
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
the exposure cap, the promotion gate and both kill switches precisely so a halt cannot trap a
position; expiry walked around all of it.

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
  record them is the exact failure mode a split switch would allow. All five selectors —
  order adapter, P&L ledger, position book, daily counter, canary registry — read the same
  variable, and a test asserts that list is exactly those five.
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
missing, expired or tampered budget yields the blocking defaults above, so there is no cap an
operator can set outside the registered record. Only three things stay env, because a phrase
proving intent and a halt are operator state rather than registered caps:
`MVP_LIVE_CONFIRMATION`, `MVP_LIVE_CANARY_CONFIRMATION`, `MVP_LIVE_MANUAL_KILL_SWITCH`.
Both guards **require** their `limits` argument (no `from_env()` fallback), so the question
"which numbers was this order judged against?" has exactly one answer.

**An unconfigured loss limit counts as breached.** `daily_loss_limit_breached(None)` and `(0)`
both return `True`. This is the single most important line in `live_pnl.py`.

**A cap above the absolute ceiling is refused, not clamped.** 200 USDT is the hard ceiling a
configured cap can never exceed. Silently resizing an order would desync its size from the
decision that approved it.

**A missing notional is never back-filled from the cap.** The cap is a ceiling, not a size.

**Guards accumulate; they never short-circuit.** The operator sees every reason at once.

**Damaged evidence is no evidence.** Both the P&L history and the canary registry are verified
reads — self-hash plus duplicate-id detection. A tampered or unparseable row raises rather than
resolving, and for promotion it counts as **zero** clean orders, never as the last good number.
A non-numeric P&L amount raises too: reading it as zero would understate a loss and could clear
a breaker that should be tripped.

## Two decisions worth stating plainly

**The reduceOnly close path is exempt** from the loss breaker, the caps, the daily count, the
promotion gate, and both kill switches (Thomas, 2026-07-23). A halt that traps you in a losing
position is more dangerous than the halt was meant to prevent. What survives is the structural
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
> canary.

- [ ] `git pull`. Before 2026-07-26 `resolve_live_order_limits` dropped `canary_confirmation`, so
      an older checkout refuses **every** canary with "canary confirmation phrase not present".
- [ ] **Activate the Core on this machine** (`CLAUDE.md` → "Core activation"). Since the live order
      path builds a P5 PermissionDecision bound to an active Core, a machine without one refuses
      with `CORE_NOT_ACTIVATED` *before* the order — governance is prepared before money moves.
- [ ] Configure the **read-only account feed**: `MVP_ACCOUNT_FEED=binance_futures_account` plus
      `BINANCE_ACCOUNT_API_KEY` / `BINANCE_ACCOUNT_API_SECRET`. The canary script refuses outright
      without it — open exposure would be unknown, and the exposure cap cannot be honored on a
      guess.
- [ ] Configure the **read-only market-data feed**: `MVP_MARKET_DATA=binance_futures` plus a
      `network_access` grant for the `binance_futures` provider
      (`scripts/activate_safety_flag.py`). Public endpoints, no API key — the grant is for
      crossing the network, not for a secret. **A canary precondition since 2026-07-26**: the
      script checks the `--notional` you declare against what `--quantity` actually implies at
      the venue's latest price, and without this feed the mock collector is selected. Its price
      is a hash of the symbol rather than a market, so the check refuses
      (`ORDER_NOTIONAL_PRICE_UNKNOWN`) instead of clearing a real order against a fabricated
      number. The readiness board reports this as `market_data_visibility`; without it **no
      canary evidence can be earned at all.**
- [ ] Create a **separate** order-capable live API key: enable Futures, **disable withdrawals
      and internal transfer**, IP-whitelist it. Keep it distinct from the read-only account key.
      `MVP_LIVE_ORDER_API_KEY` / `MVP_LIVE_ORDER_API_SECRET`.
- [ ] Register the budget — the caps come from the record, never from env. Approved starting
      values (Thomas, 2026-07-23): 60 USDT per order, 2 orders per day, 120 USDT open exposure,
      20 USDT daily loss, against the 200 USDT absolute ceiling.

      ```
      python -m scripts.register_live_trading_budget --registered-by thomas \
          --max-order-notional 60 --max-daily-order-count 2 \
          --max-open-notional 120 --daily-loss-limit 20 --absolute-max-notional 200
      ```
- [ ] Set the confirmation phrase **for the capability you are about to use**. They are
      deliberately distinct, so pasting the wrong one authorizes nothing:
      `MVP_LIVE_CANARY_CONFIRMATION` for canaries, `MVP_LIVE_CONFIRMATION` for autonomous trading.
      A canary needs only the first.
- [ ] Set `MVP_LIVE_TRADING=real`. **This is now the entire gate** — there is no grant to mint
      since 2026-07-28, so this one line selects every real live component at once. Confirm it
      on the board (`live_trading_opt_in` PASS) before continuing rather than assuming.

**Gate 3 — promotion evidence: 3 clean canary orders**
- [ ] Confirm the board first: `python -m runtime.mvp_runtime.crypto.live_readiness`. Everything
      except `canary_evidence` must be PASS. That one row staying FAIL at `0/3` is **expected** —
      it is the single check a canary is exempt from, and the canary is what earns it.
- [ ] Place canary orders until three are clean. **One exists**, from 2026-07-16 in the source
      system; it did not migrate, so the count here is currently 0. Each canary is one small
      real order placed deliberately to prove signing, submission, and reconciliation.

      ```
      python -m scripts.place_canary_order --symbol BTCUSDT --quantity <qty> --notional <qty x price>
      ```

      `--notional` is **never** back-filled from the cap — state it truthfully, at or under the
      60 USDT per-order cap and above the venue's own minimum. Since 2026-07-26 that is
      **checked, not trusted**: the script reads the venue's latest closed 1m price and refuses
      with `ORDER_NOTIONAL_UNDERSTATED` when your declaration falls more than 1%
      (`live_order.NOTIONAL_TOLERANCE_FRACTION`) below `quantity x price`. Over-declaring passes
      — it only makes every cap stricter. An unreadable, synthetic or stale price refuses with
      `ORDER_NOTIONAL_PRICE_UNKNOWN` rather than waving the order through.

      Work the quantity out from the price at the moment you place it. There is deliberately no
      example number here: the one that used to stand in this file and in the script's own
      docstring (`--quantity 0.001 --notional 60`) was written against an older BTC price and
      understated the real order by ~7% once BTC passed 64,512 — following the documentation
      produced the wrong declaration. Check `clean: True` in the output; anything else does not
      count toward the three.
- [ ] Close each canary position on the venue afterwards — canaries only **open**.
- [ ] Budget the calendar: the daily order cap is **2**, so three clean canaries take **at least
      two UTC days**. Raising the cap to finish sooner would defeat what the canary proves —
      that the plumbing works at the conservative boundary.

**Gate 4 — verify the gate before any autonomous run**
- [ ] `python -m runtime.mvp_runtime.crypto.live_readiness` reports READY. A refusal names
      exactly what is missing; fix it rather than working around it.

**Gate 5 — first supervised cycles**
- [ ] Watch the first entries and closes live. Confirm each entry reconciles and each position
      closes on stop-loss, take-profit, or time.
- [ ] Watch the daily-loss breaker and the open-exposure cap behave.

**Standing controls — know these before you start**
- **Stop new entries immediately:** the operator console `kill` verb. It writes control state,
  so it lands on the running service at its next guard, and closes remain permitted
  (`kill_blocks: external_execution`). Since 2026-07-28 this is the *only* control that acts on
  a running scheduler — it replaced "delete the grant file", which no longer exists.
- **Softer halt, next restart:** set `MVP_LIVE_MANUAL_KILL_SWITCH=true`. Refuses entries, closes
  still permitted. Env-based, so it needs the service restarted.
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
