# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-07-24** (end of the Kalshi/Polymarket roadmap session).
Keep it current — when a milestone ships, tick its box or delete it here in the same PR.

Authoritative detail for each item lives in the linked roadmap docs; this file is the index.

---

## In-flight PRs (snapshot 2026-07-24 — being merged, possibly from another machine)

- [ ] **#145** M1: difficulty triage (상/중/하) + LLM orchestration roadmap — `feat/difficulty-triage`
- [ ] **#143** docs: prediction-market trading roadmap v0.1 (PM0–PM3) — `docs/prediction-market-roadmap`
- [ ] **#142** LP4 governance: a live-order PermissionDecision is buildable at P5 (grants nothing) — `feat/live-order-permission`
- [x] #146 memory console (/memory, /promote) — merged 2026-07-24
- [x] #144 promote organization architecture to active Goal doc — merged 2026-07-24

> Check live state with `gh pr list`. Once #143 and #145 merge, the two roadmaps below
> are on `main` and visible from a plain clone (until then: `git fetch` + read the branch).

---

## A. Prediction-market trading (Kalshi / Polymarket) — NEW this session

Roadmap: [`docs/PREDICTION_MARKET_ROADMAP_V0.1.md`](PREDICTION_MARKET_ROADMAP_V0.1.md) (PR #143).
Phasing: observe (no money) → paper (no external effect) → approval-gated live (per-order approval).

- [ ] **PM0 — venue access** (operator-only, no code): Kalshi international signup (KYC), Polymarket
      Polygon/USDC wallet, and the **Korean regulatory judgment call** (grey area). Blocks PM3 only,
      not PM1/PM2.
- [ ] **PM1 — observe-only pipeline** (2–3 PRs; no money, no account needed):
  - [ ] Read-only venue adapters (Kalshi REST; Polymarket Gamma + CLOB) behind
        `kalshi_market_data` / `polymarket_market_data` safety flags, DEGRADED semantics.
  - [ ] Event-pair matching — auto candidate generation + **operator confirmation per pair**
        (the hardest real engineering here; a wrong pair fakes arbitrage forever).
  - [ ] Fee-adjusted opportunity detector (Kalshi fee ≈ $0.07·P·(1−P)/contract; Polymarket gas+spread).
  - [ ] Observation records + `pm_scan` R6 scheduler template ⚠️ (cadence + per-scan market cap).
  - [ ] **Exit artifact:** 2–4 week report — frequency × net margin × **persistence duration** per
        strategy. Persistence decides whether PM3 (minutes of approval latency) can ever catch it.
- [ ] **PM2 — paper trading** (1–2 PRs): pessimistic fill model (taker + book depth + fees), virtual
      portfolio, **hold-to-resolution** (also measures cross-venue resolution mismatch).
  - [ ] ⚠️ Thomas sets **PM3 entry criteria as numbers** before PM2 ends.
- [ ] **PM3 — approval-gated live orders** ⚠️⚠️ — **triple-blocked**: PM0 done + PM2 criteria met +
      the live-execution governance packet (section C) implemented. Per-order R9 approval +
      single-use consumption behind `kalshi_trade` / `polymarket_trade` grants. Third consumption
      scope decision required.
- [ ] Resolve the **7 open decisions** in the roadmap's decision register (pm_scan template,
      LLM-assisted matching on/off, PM3 numeric criteria, trading-budget record shape, third
      consumption scope, Korean legal call, PM4 bounded autonomy).

**Out of scope (each its own future decision):** PM4 bounded autonomy, market making, directional/news
trading, leverage, any US-context Polymarket access.

---

## B. LLM orchestration (M-series) — request → tiered model → verify → deliver

Roadmap: `docs/LLM_ORCHESTRATION_ROADMAP_V0.1.md` (lands on `main` with PR #145).

- [x] **M0** env cleanup (done 2026-07-24).
- [ ] **M1** difficulty triage 상/중/하, observe-only — **in PR #145** (about to merge).
- [ ] **M2** ⚠️ difficulty → OpenRouter tier model. Needs Thomas decision: three tier provider ids
      (`openrouter_light/standard/heavy`) + their model slugs + a local grant per tier.
      Fail-closed: no tier grant → degrade to base chain + audit `TIER_DEGRADED`.
- [ ] **M3** ⚠️ verify-fail → bounded LLM revision loop. Hard cap **1** retry, pre-allocated retry
      budget, both retry and give-up audited. (An endless REVISE loop happened live before —
      re-introduction conditions are mandatory.)
- [ ] **M4a** ⚠️ crypto: second-pass sort by win-rate + risk-reward on top of the robustness filter.
- [ ] **M4b** ⚠️ crypto: put the strategy proposer on a schedule (needs per-run + backlog caps).
- [ ] **M5a–d** ⚠️ trial-and-error learning loop: correction → working-memory CANDIDATE → Thomas
      promotes to VALIDATED → planner retrieves it for similar requests → (later) programization.
      Operator-gated by design.

---

## C. Crypto live execution — the governance packet + the order code

Decision record: `docs/runtime-contracts/LIVE_EXECUTION_GOVERNANCE_V0.1.md` (decided 2026-07-23,
**not implemented**). Status: `docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`.
**PR #142 is the first step of this track** (a live-order PermissionDecision buildable at P5, grants nothing).

- [ ] **Governance implementation (steps 1–10)** — blocked until `feat/cost-budget-ledger` (B2 spend
      gate) merges first, then rebase onto it:
  - [ ] `permission_decision.v0.4` — add `FINANCIAL_APPROVED_TRADING_USE` scope (first bump since v0.3).
  - [ ] Policy: scope in `policy_dispositions.EXECUTE_AND_REPORT`; define `p5_policy_gate`;
        `financial_transaction_execution_implemented: true` **only when LP4 merges** (leave
        `financial_executor_enabled: false` byte-for-byte).
  - [ ] New closed schema `live_trading_budget.v0.1` (registered trading caps, self-hashed).
  - [ ] New narrow role `execution.live_trader` — P5, `external_action_allowed: true`, candidate,
        its own `ROLE_GOVERNANCE` approval.
  - [ ] Update validator assertions + `require_doc_tokens`; **regenerate both replay bundles**
        (CRLF-normalized SHA; `rebuild_bundle` has no CLI entrypoint).
- [ ] **LP4** order adapter + **LP5** position kernel / cycle routing — **code does not exist yet**.
- [ ] **≥ 3 clean canary orders** before any autonomous run (currently **0** migrated; 1 existed in
      the frozen source system, did not migrate).
- [ ] Standing finding: the router is **symbol-starved** — the cycle runs BTCUSDT only while the pool
      is mostly other symbols, so most strategies are never evaluated.

> Real money. The full operator go-live checklist (grants, confirmation phrase, caps, kill switches)
> is in `CRYPTO_LIVE_EXECUTION_V0.1.md`. Claude does not run it, does not handle real keys, and does
> not enable live trading — every step there is Thomas's.

---

## Per-machine setup that does NOT travel via git

A fresh machine has the code but not the local runtime state (gitignored, per CLAUDE.md). To actually
*run* the agent there, re-do the local activation once:

- Core activation pointer: `.runtime_governance_state/CURRENT_CORE_RELEASE.yaml`
- Safety-flag grants: `.runtime_governance_state/safety_flag_activations/*.json`
- Control state + ledger + schedules under `.runtime_governance_state/`

None of this is "planned work" — it is per-machine state you re-establish with the CLAUDE.md
"Core activation" steps + `scripts/activate_safety_flag.py`.

---

## How to use this file from another computer

```
git pull
```

Then open this file, or just ask Claude Code "남은 작업이 뭐야?" — it will read
`docs/REMAINING_WORK.md` and list the unchecked items above.
