# Crypto Live Execution v0.1

**Status:** Partially implemented — LP1, LP2, LP3, LP6 shipped. **No order path exists.**
**Owner:** Thomas
**Authority:** None. The canonical Governance Policy (`governance/GOVERNANCE_POLICY.yaml`)
owns every rule this describes. The governance decisions this work still needs are recorded
separately in `LIVE_EXECUTION_GOVERNANCE_V0.1.md` and **have not been implemented**.

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
| LP4 order adapter | `execution/live_canary_adapter.py` | **no** | — |
| LP5 position kernel + routing | `execution/live_position_kernel.py` (L5/L3/L6) | **no** | — |

Everything shipped so far either **reads** or **refuses**. Nothing in
`runtime/mvp_runtime/crypto/` can place, amend, or cancel an order at a venue. That is not a
disabled flag; the code does not exist.

## Effect-tier mapping

| Behavior | Effect | Expression here |
|---|---|---|
| Account balance / positions / realized P&L | External read | `INTERNAL_READ` · ALLOW behind its own `binance_futures_account` grant; failure **degrades** (`ACCOUNT_DATA_DEGRADED`), never blocks — the R3/`MARKET_DATA_DEGRADED` precedent |
| Realized live P&L ledger + daily-loss breaker | Internal state + validation | Records behind the `live_trading` grant; the breaker is a pure read every caller can make ungated |
| Order intent construction, idempotency, final guard | Internal compute | Pure functions. No gate, because computing a refusal is not a capability |
| Canary promotion evidence | Internal record creation | Append behind the `live_trading` grant; reads ungated and verified |
| **Live order submission** | **External + financial** | **Not implemented.** Needs the decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`: a new `FINANCIAL_APPROVED_TRADING_USE` scope at P5, a P5-capable role, a registered trading budget, and a defined P5 policy gate |

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

**Zero means "not configured", never "unlimited".** Every cap (`MVP_LIVE_MAX_ORDER_NOTIONAL_USDT`,
`MVP_LIVE_MAX_DAILY_ORDER_COUNT`, `MVP_LIVE_MAX_OPEN_NOTIONAL_USDT`,
`MVP_LIVE_DAILY_LOSS_LIMIT_USDT`) defaults to 0, and 0 blocks. A missing risk limit is the
most dangerous state a trading system can be in, so it must read as halted.

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
- [x] Paper trading on real data shows positive expectancy over a sustained window
      (2.36R over 114 closed trades as of 2026-07-23). Check with
      `python -m runtime.mvp_runtime.crypto.dashboard`.
- [ ] The active pool is populated with strategies you trust. The former symbol-starved finding
      is resolved: a crypto schedule with an empty request now fans out over every
      ``(symbol, timeframe)`` the pool routes on — plus every context that holds an open paper
      position, so a demoted strategy's position still settles — via
      ``cycle.run_pool_cycle`` (a named ``SYMBOL [TIMEFRAME]`` request still pins one context as
      an operator override). One remaining narrower limit: a strategy scoped to several symbols
      is still evaluated only on its primary ``symbol_scope[0]`` (the router's keying), so
      multi-symbol strategies are not multiplexed across their whole scope.

**Gate 1 — the code must exist**
- [ ] LP4 (order adapter) and LP5 (position kernel + cycle routing) merged. **Blocked** on the
      governance decisions in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`, which are recorded but not
      implemented.

**Gate 2 — promotion evidence: 3 clean canary orders**
- [ ] Place canary orders until three are clean. **One exists**, from 2026-07-16 in the source
      system; it did not migrate, so the count here is currently 0. Each canary is one small
      real order placed deliberately to prove signing, submission, and reconciliation. Close
      each canary position on the venue afterwards — canaries only open.
- [ ] Verify with `python -m runtime.mvp_runtime.crypto.live_readiness`.

**Gate 3 — configure the boundary (conservative first)**
- [ ] Create a **separate** order-capable live API key: enable Futures, **disable withdrawals
      and internal transfer**, IP-whitelist it. Keep it distinct from the read-only account key.
- [ ] Set the caps. Approved starting values (Thomas, 2026-07-23): 60 USDT per order, 2 orders
      per day, 120 USDT open exposure, 20 USDT daily loss, against the 200 USDT absolute
      ceiling.
- [ ] Set `MVP_LIVE_CONFIRMATION` to the live-trading phrase. It is deliberately distinct from
      the canary and testnet phrases, so pasting the wrong one authorizes nothing.
- [ ] Mint the `live_trading` grant (command above).

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
