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
| LP4 order adapter | `execution/live_canary_adapter.py` | **yes** (2026-07-25) | **YES** — `live_execution.py`, behind the `live_trading` grant + the order key + a guard PASS |
| LP5 position kernel + routing | `execution/live_position_kernel.py` (L5/L3/L6) | **almost** — 5.1 state/reconciliation, 5.2 sizing, 5.3 the entry decision **and the executing leg**, 5.4 the outcome bridge | the executing leg can, with an **injected** adapter — but nothing autonomous may import it (tripwire test), so **cycle routing is the only piece left** |

The honest summary changed on 2026-07-25 and is worth stating without softening: **an order path
now exists.** What still holds is that nothing reaches it on its own. Every other module either
**reads** or **refuses**, and the one that can send requires, simultaneously, the operator's
per-machine `live_trading` grant, the order-capable API key, a registered budget, the
autonomous confirmation phrase (or the separate canary phrase), both kill switches clear, and a
guard PASS — and even then it is only reached from the deliberate
`scripts/place_canary_order.py`, one canary at a time.

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
| Realized live P&L ledger + daily-loss breaker | Internal state + validation | Records behind the `live_trading` grant; the breaker is a pure read every caller can make ungated |
| Order intent construction, idempotency, final guard | Internal compute | Pure functions. No gate, because computing a refusal is not a capability |
| Canary promotion evidence | Internal record creation | Append behind the `live_trading` grant; reads ungated and verified |
| **Live order submission** | **External + financial** | **Implemented** (LP4, 2026-07-25) under the decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`: `FINANCIAL_APPROVED_TRADING_USE` at P5, the `execution.live_trader` role (**candidate, non-routable** — activating it is a separate `ROLE_GOVERNANCE` approval), a registered `live_trading_budget.v0.1`, and the `p5_policy_gate`. Reached only from `scripts/place_canary_order.py`; `financial_executor_enabled` stays `false` |
| **Live entry decision** (LP5.3) | Internal compute | Pure functions — `live_entry.plan_live_entry` decides and refuses; it holds no adapter, so deciding is not a capability |
| **Live executing leg** (LP5.3) | **External + financial**, when given an adapter | `live_leg.execute_live_entry` / `execute_live_exit`. The adapter is **injected**, never selected, so the module cannot reach a venue on its own; it also refuses without a governance record. No autonomous entry point may import it (`test_no_autonomous_entry_point_reaches_the_live_order_path`), and the readiness board reports that as the `autonomous_routing_wired` row |
| **Venue trading rules** (`exchangeInfo`) | External read | `INTERNAL_READ` · ALLOW on the existing `binance_futures` market-data grant; failure **degrades** (`LIVE_FILTERS_DEGRADED`) and sizing then refuses |

## One grant is the whole switch

Every live-side capability shares a single per-machine provider grant, `live_trading`,
carrying **both** `network_access` and `filesystem_write`:

```
scripts/activate_safety_flag.py --provider-id live_trading \
    --flags network_access,filesystem_write --authority-level P5 \
    --reason "..." --ttl-minutes 43200
```

The consequences are deliberate:

* It cannot be half-enabled. Orders reaching the venue while the P&L ledger silently fails to
  record them is the exact failure mode a split grant would allow.
* **Deleting the grant file is a live revocation.** `assert_authorization` re-reads the record
  at every egress, so order submission, the P&L ledger, the daily counter, and the canary
  registry all stop at once.
* It expires. The 30-day TTL cap applies, so live capability lapses rather than persisting by
  forgetfulness.
* The env var alone fails closed. `MVP_LIVE_TRADING=real` without a valid local grant refuses.

The account read (LP1) deliberately gets its **own** grant, `binance_futures_account`, not
this one — reading balances needs a key with a wider blast radius than public market data, and
it must be scoped, expired, and revocable independently of the ability to trade.

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
- [ ] Mint the `live_trading` grant (command above) and set `MVP_LIVE_TRADING=real`. The env var
      alone fails closed.

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
      60 USDT per-order cap and above the venue's own minimum. Check `clean: True` in the output;
      anything else does not count toward the three.
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
- **Stop new entries immediately:** delete the `live_trading` grant file. Revocation is live;
  open positions can still close.
- **Softer halt:** set `MVP_LIVE_MANUAL_KILL_SWITCH=true`.
- **Whole-runtime halt:** the operator console `kill` verb. Blocks live entries via
  `kill_blocks: external_execution`; closes remain permitted.
- **Daily-loss breaker:** entries halt for the UTC day once realized live loss reaches the
  configured limit, and resume the next UTC day.

## Deliberately not ported

The x10 SDK, the streamlit dashboards, the source's backtesting UI, and the legacy
`live_guard`/`order_executor` review-only surfaces. The source's separate **signed testnet**
boundary is also not ported: this runtime's mock/paper path already covers pre-live rehearsal,
and a second venue with its own keys, hosts, phrases, and counters is more surface than it
earns. If testnet rehearsal is wanted later it is a deliberate addition, not an oversight.
