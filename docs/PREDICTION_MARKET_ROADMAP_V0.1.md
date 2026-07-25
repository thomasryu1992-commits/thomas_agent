# Prediction-Market Trading Roadmap — v0.1 (Kalshi / Polymarket)

**Status:** planning record (not a contract). Captures the Thomas-requested build-out of a
prediction-market trading capability on Kalshi and Polymarket. Each milestone is a
branch → PR → gates → merge; one capability per PR. Milestones that require an explicit
Thomas decision are marked ⚠️.

Author date: 2026-07-24.

## Why this exists

Thomas asked for an auto-trading agent over Kalshi and Polymarket, with the goal of high
returns. The honest premise this roadmap is built on: **no design guarantees returns; design
controls where the edge comes from and where the losses stop.** In prediction markets a bot
realistically earns from three sources, in this order of reliability:

1. **Cross-venue arbitrage** — the same event priced differently on Kalshi vs Polymarket.
   Market-neutral in theory; in practice eroded by fees, gas, and **resolution-criteria
   mismatch** between the two venues (the hidden risk that decides this strategy).
2. **Intra-venue structural arbitrage** — YES+NO summing below $1, or a multi-outcome
   market's probabilities summing away from 100%. Lowest risk, thin and infrequent.
3. **Market making** — spread capture with inventory risk. Requires news-triggered quote
   pulling; deferred (out of scope for every phase below).

Directional/news trading is deliberately **not pursued**: it competes on speed and
information against dedicated infrastructure, and its expected edge is the least verifiable.

The phasing follows the repo's own doctrine: observe (no money) → paper (no external
effect) → approval-gated live (every order individually approved). **Bounded autonomy
(a "PM4") is explicitly out of scope** until the phases below produce evidence, and is its
own future Thomas decision.

## PM0 — venue access preconditions (operator-only, no code)

Verified 2026-07-24 (web sources; re-verify at signup):

- **Kalshi** opened internationally in October 2025 (~140 countries, South Korea listed as
  available). KYC + country-of-residence checks at signup; international deposits are
  card / wire / crypto (no ACH/PayPal).
- **Polymarket** does not geoblock South Korea. Access is wallet-based: USDC on Polygon,
  no account approval, but funding from Korean exchanges passes through travel-rule
  transfer flows.
- **Korean regulatory status is a grey area** (gambling-adjacent). This is Thomas's own
  judgment call, made outside this repo; the roadmap only requires that PM3 not start
  until it is made.
- **PM1 needs none of the above** — both venues expose public market data; observation
  requires no account and no funds. PM0 blocks PM3, not PM1/PM2.

## Relationship to the crypto pipeline

This is the second trading domain after `CRYPTO_PIPELINE_V0.1.md` / 
`CRYPTO_LIVE_EXECUTION_V0.1.md`, and it deliberately **reuses that work's shape, not a
parallel invention**:

- New package `runtime/mvp_runtime/predmarket/` mirroring `crypto/` (market_data /
  opportunities / paper / …). Venue adapters differ; the governed chokepoints are the same.
- Market-data reads follow the `binance_market_data` precedent: per-venue safety-flag
  grants, `INTERNAL_READ` ALLOW, backend failure **degrades** (`MARKET_DATA_DEGRADED`),
  never blocks.
- Paper trading follows the crypto C5 / R8 pattern: gated flag, DryRun default,
  kill-switch bound.
- **A live prediction-market order is the same governance object as a live futures order**:
  P5 EXTERNAL_ACTION reaching an outside counterparty. PM3 therefore rides the already-
  decided (2026-07-23, unimplemented) packet in `LIVE_EXECUTION_GOVERNANCE_V0.1.md`
  — `FINANCIAL_APPROVED_TRADING_USE`, the `execution.live_trader` role, the registered
  trading budget, the `p5_policy_gate`. **This roadmap adds no second financial-governance
  track.** What PM3 adds on top is stricter, not looser: a per-order R9 approval.

## Effect-tier mapping

| Behavior | Effect | Expression (precedent) |
|---|---|---|
| Kalshi / Polymarket market + orderbook reads | External read | `INTERNAL_READ` · ALLOW behind `kalshi_market_data` / `polymarket_market_data` grants; failure → degrade + audit (R3 / `MARKET_DATA_DEGRADED`) |
| Event-pair matching (same event across venues) | Internal compute + operator config | Deterministic candidate generation (`INTERNAL_ANALYSIS` ALLOW); **pair confirmation is an operator action** over the verified channel / CLI, stored in local state, audited |
| Opportunity detection (fee-adjusted) | Internal compute | `INTERNAL_ANALYSIS` · ALLOW, audited (R7.2 triage precedent). Deterministic — no model call |
| Observation / paper records | Internal record creation | Versioned payloads (`pm_observation.v0`, `pm_paper_trade.v0`) inside existing record envelopes — **no new closed schema now** (crypto C1 verdict); a closed schema lands with PM3, where the approval fingerprint needs a canonical shape |
| Paper position open/close | Internal reversible write | EXECUTE_AND_REPORT behind a `pm_paper_trading` grant (own grant, so revocation is independent of the crypto paper track); DryRun default; kill-switch bound |
| Recurring scan | Scheduled execution | New R6 template `pm_scan` (the `crypto_pipeline` template precedent); `kill_blocks: scheduler_execution` applies automatically |
| **Live order submission** | **External + financial (P5)** | **Not buildable until the `LIVE_EXECUTION_GOVERNANCE_V0.1.md` packet is implemented.** Then: per-order R9 approval + single-use consumption behind per-venue `kalshi_trade` / `polymarket_trade` grants |

## Milestones

### PM1 — observe-only pipeline *(2–3 PRs; one small ⚠️: the `pm_scan` template)*

**Goal: establish with data whether exploitable mispricings exist — how often, how large
net of fees, and how long they persist.** Persistence is the load-bearing number: it decides
whether approval-gated trading (minutes of latency) can ever catch the opportunity, i.e.
whether PM3 is worth building at all.

- **Venue adapters** (read-only): Kalshi public REST; Polymarket Gamma (markets) + CLOB
  (books). Selection through `safety_gate.select_gated`; mock backends default; env alone
  fails closed.
- **Event-pair matching** — the hardest real engineering here. Deterministic candidate
  generation (category + close-date + text similarity; optionally LLM-assisted under the
  existing triage-style budgeted call), then **operator confirmation per pair**. A wrong
  pair manufactures fake arbitrage signals forever, so confirmation is human by design.
  Confirmed pairs live in `.runtime_governance_state/predmarket/pairs.jsonl`, audited.
- **Opportunity detector**, fee-adjusted only: Kalshi's fee is a price function
  (≈ $0.07 × P × (1−P) per contract, rounded up) — unadjusted observations would be
  systematically fake. Polymarket side models gas + spread cost.
- **Observation records**: snapshot + "had we entered here" hypothetical, appended to the
  run's records ledger, every scan audited.
- **Scheduler**: `pm_scan` template ⚠️ (cadence and per-scan market cap are the decision).
- **Exit artifact**: after 2–4 weeks, a report of opportunity frequency × net margin ×
  **persistence duration** per strategy, plus pair-mismatch incidents observed.

### PM2 — paper trading *(1–2 PRs; ⚠️ Thomas sets the PM3 entry criteria)*

**Goal: net-of-costs positive expectancy under a pessimistic fill model, with zero external
effect.**

- **Conservative fill model**: always cross the spread (taker), fill only to visible book
  depth, all fees + gas included. An optimistic fill model is the classic way paper trading
  lies to its owner; pessimism is structural here.
- **Virtual portfolio** in `.runtime_governance_state/predmarket/paper_portfolio.json`;
  every simulated open/close audited (audit-every-outcome).
- **Hold to resolution**: most prediction-market arbitrage realizes at settlement, so paper
  positions track to actual market resolution — which also measures, live, how often the
  two venues **resolve the same event differently** (the cross-venue strategy's real risk).
- ⚠️ **Thomas fixes the PM3 entry criteria as numbers before PM2 ends**, e.g.: N consecutive
  weeks net-positive after costs, persistence median ≥ X minutes for the traded strategy
  class, zero unmodeled resolution-mismatch losses. Without pre-committed numbers, the
  live decision gets made by feel — the exact failure this repo exists to prevent.

### PM3 — approval-gated live orders ⚠️⚠️ *(blocked on: PM0, PM2 criteria met, and the live-execution governance packet being implemented)*

**Goal: the narrowest possible live crossing — every order individually fingerprinted,
approved, and spent.**

- **Governance**: no new track. Requires the `LIVE_EXECUTION_GOVERNANCE_V0.1.md`
  implementation sequence (permission_decision v0.4 scope, P5 role, registered budget,
  `p5_policy_gate`) to have landed — that packet was decided for crypto but is
  venue-neutral by construction. PM-specific additions:
  - the registered trading-budget record covers these venues (either
    `live_trading_budget.v0.1` gains venue rows or a sibling record — ⚠️ decide with the
    schema author),
  - per-venue safety flags **`kalshi_trade`** and **`polymarket_trade`** — separate grants
    because the credentials differ in blast radius (Kalshi API key vs. a Polymarket
    **wallet signing key**), each independently scoped, TTL-capped, revocable by file
    deletion (the `live_trading` precedent).
- **Per-order flow** (stricter than the scope alone requires): R9 ask whose fingerprint
  binds *venue, market id, side, size, limit price, expiry* → Telegram `/approve` →
  single-use consumption (R10 pattern: **CONSUMED before submission** — a failed submit is
  spent-but-unsent, never re-spendable) → pre-submit revalidation re-reads the current
  book and refuses on adverse movement (`PRICE_MOVED`) → submit → poll fills → reconcile →
  audit. ⚠️ This is the **third consumption scope** (after memory promotion and candidate
  trials) — its own explicit widening decision.
- **Secrets**: the Polymarket signing key is the most sensitive secret this runtime would
  ever hold. A dedicated signing wallet holding only working balance; key in env/local
  keystore only; ledger and audit carry metadata, never material (standing rule).
- **Stated honestly**: per-order approval means minutes of latency, so PM3 can only capture
  **persistent** mispricings (hold-to-resolution arbitrage, structural gaps). Sub-minute
  cross-venue arbitrage is structurally out of reach at this phase; reaching it is the PM4
  bounded-autonomy decision nobody has taken.

### Out of scope for every phase above

Bounded autonomy (position/loss-capped self-directed trading), market making, directional
or news-driven trading, leverage of any kind, and anything that touches Polymarket from a
US context. Each returns only as its own explicit Thomas decision.

## Sequence

```
PM0 (operator: accounts + legal call) ──────────────┐
PM1 (observe, no funds) → PM1 report → PM2 (paper) → ⚠️ criteria met? → PM3 (live, per-order approval)
                                                     └─ requires LIVE_EXECUTION_GOVERNANCE packet implemented
```

PM1 can start immediately and in parallel with PM0. PM3 is triple-gated: venue access,
PM2 numbers, governance implementation.

## Decision register

| # | Decision | Phase | Status |
|---|---|---|---|
| 1 | `pm_scan` scheduler template (cadence, market cap) | PM1 | ⚠️ open |
| 2 | LLM-assisted pair matching on/off (deterministic-only is the default) | PM1 | ⚠️ open |
| 3 | PM3 entry criteria as numbers | PM2 | ⚠️ open — must precede PM2 end |
| 4 | Trading-budget record shape for prediction venues | PM3 | ⚠️ open |
| 5 | Third consumption scope (per-order spend) | PM3 | ⚠️ open |
| 6 | Korean regulatory judgment | PM0 | Thomas, outside the repo |
| 7 | PM4 bounded autonomy | — | not on the table |

## Invariants every milestone keeps

- Reuse first; the only genuinely new surfaces are venue adapters and the `pm_scan`
  template — every gate, approval, consumption, and audit mechanism already exists.
- Fail-closed: missing/uncertain/unauthorized → degrade-and-audit or refuse, never guess;
  an env var alone never opens a network path.
- Every observation is fee-adjusted or it is not recorded; every paper fill is pessimistic
  or it is not counted.
- No live order without: PM0 done, PM2 criteria met as pre-committed numbers, the P5
  governance packet implemented, a per-venue grant minted, and a per-order approval spent.
