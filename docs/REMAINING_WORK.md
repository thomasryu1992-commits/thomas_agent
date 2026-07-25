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
- [x] **M1** difficulty triage 상/중/하, observe-only — merged (PR #145).
- [x] **M2** difficulty → OpenRouter tier model — merged (PR #149). Per-tier grants + model slugs
      stay the local operator step; until minted, every run degrades cleanly to the base chain.
- [x] **M3** verify-fail → bounded LLM revision loop (opt-in `--revise`, hard cap 1) — merged (PR #150).
- [x] **M4a** crypto: second-pass win-rate + risk-reward ranking — merged (PR #148).
- [x] **M4b** crypto: the strategy proposer on a schedule (`crypto_propose` kind) — done. Per-run
      cap (existing) + unreviewed-backlog cap (distinct accepted-but-uninstalled families; skip +
      audit `skipped_backlog_full:N` past 12, 30-day window). Installing a family clears its
      backlog slot. Also registered the proposal ledger kind (a latent persist bug).
- [x] **M5a** correction → working-memory CANDIDATE — done. A successful M3 revision (REVISE→PASS)
      or a `/feedback bad <note>` mints a correction candidate (ALLOW-tier, audited on the
      memory-event stream, CANDIDATE-only). `runtime/mvp_runtime/memory.py`
      (`build_correction_candidate`/`build_learning_event`), wired in `pipeline.py` +
      `operator_feedback.py`.
- [ ] **M5b** Thomas promotes useful correction candidates to VALIDATED — already available via the
      existing R9/`/promote` door; no new code, it is the operator's explicit yes.
- [x] **M5c** a promoted VALIDATED correction feeds back as a correction to *apply* (`[V#]`,
      distinctly framed) — done. `promote_candidate` carries the correction marker forward;
      `worker._validated_context` frames it. Known limit: only revision-path corrections are
      promotable (they carry origin); feedback-path corrections stay `[M#]` until origin can be
      reconstructed from the delivered run.
- [x] **M5d** repeated identical corrections surface at the programization review as a read-only
      correction lineage — done 2026-07-25 (option C, reuse-first: `correction_lineage_for_pattern`
      + `programization_cli lineage <pattern_id>`; no new schema/counter). Codifying stays the
      existing operator-gated programization flow.

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
  - [x] New closed schema `live_trading_budget.v0.1` (registered trading caps, self-hashed) —
        done 2026-07-25 (schema + `live_budget.py` + `register_live_trading_budget.py`).
  - [x] Step 6b: the guard reads the registered budget as authoritative (over env caps) — done
        2026-07-25 (`resolve_live_order_limits` + `budget_registered` guard check + the readiness
        `registered_budget` row). No live order without a valid registered budget. Grants nothing.
  - [x] New narrow role `execution.live_trader` — P5, `external_action_allowed: true`, **candidate
        (non-routable)** — done 2026-07-25 (contract + index-only registry entry + hash; passes
        contract-consistency + release gate). Grants nothing; **activating** it (candidate →
        routable) is the separate remaining `ROLE_GOVERNANCE` approval.
  - [ ] Update validator assertions + `require_doc_tokens`; **regenerate both replay bundles**
        (CRLF-normalized SHA; `rebuild_bundle` has no CLI entrypoint).
- [~] **LP4** order adapter — **increment 1 (skeleton) done 2026-07-25**
      (`runtime/mvp_runtime/crypto/live_execution.py`: adapter protocol, DryRun default, gated
      stub, `submit_and_reconcile` + reconcile vocabulary; design record
      `LP4_ORDER_ADAPTER_DESIGN_V0.1.md`). **Increment 2a (the real signed transport +
      conditional order types) done 2026-07-25** — venue semantics verified against the official
      New Order / Query Order / error-code references (corrected: closePosition excludes both
      quantity and reduceOnly; -2013 = NOT_FOUND vs any other rejection = UNRECONCILABLE; no
      documented auto-cancel, so LP5 must cancel the surviving bracket leg).
      **Increment 2b done 2026-07-25** — `scripts/place_canary_order.py` (the deliberate
      single-canary path, entry-only, exposure read from the venue so the guard's one fail-open
      default is not used); a `canary=True` guard mode exempt from the promotion gate only (the
      chicken-and-egg: a canary earns that evidence) with its **own** confirmation phrase
      (`MVP_LIVE_CANARY_CONFIRMATION`), so neither phrase can authorize the other's capability;
      and the **lockstep governance flip** (`financial_transaction_execution_implemented: true`
      + `ORDER_PATH_IMPLEMENTED = True`, asserted to agree) with both replay bundles regenerated.
      `financial_executor_enabled` and every `runtime_effect`/`cutover` flag stay false.
      **LP4 is complete.** Nothing autonomous routes to the venue — that needs LP5.
- [~] **LP5** position kernel + cycle routing — **design record done 2026-07-25**
      (`LP5_POSITION_KERNEL_DESIGN_V0.1.md`); **no code yet**. Decisions taken: a venue-side SL+TP
      bracket at entry, and sizing = `min(risk-based, budget cap)` refusing rather than defaulting.
      Mandatory findings it records: live positions **must** use a separate `live_positions/`
      namespace + `stage: "live"` (paper keys on `(venue, symbol, timeframe)` with the same
      `binance_futures` venue string, so a shared book would let the paper cycle settle a real
      position); the venue — not the store — is the truth, so every live cycle reconciles or
      refuses; and `evaluate_live_order_guard`'s `current_open_notional_usdt=0.0` default is the
      one fail-open path and gets closed. Planned increments: LP5.1 state+reconciliation,
      LP5.2 sizing, LP5.3 the live leg, LP5.4 the outcome bridge.
- [ ] **Live outcomes are invisible to the guards** — the live outcome record has no `result_R`,
      `created_at_utc`, or strategy lineage, so `guards.run_risk_guard`, `lifecycle`, and the C6
      feedback report cannot read live results (only the daily-loss breaker can). Closed by LP5.4.
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
