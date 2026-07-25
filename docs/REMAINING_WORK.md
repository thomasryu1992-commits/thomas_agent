# Remaining Work — canonical to-do list

**This is the single place to answer "what's left to build?" from any machine.**
It is committed to git on purpose: per-machine memory does not travel between computers,
so the durable hand-off lives here. On a fresh machine: `git pull`, then read this file.

Last updated: **2026-07-25**, after LP5.4 merged (`main` = `42ce85b`). Every claim below was
re-checked against `main` and against the code it describes, not carried over.

> **The one thing to read first:** since 2026-07-25 an order path **exists** and
> `financial_transaction_execution_implemented` is **true**. Live trading still cannot start —
> `financial_executor_enabled` is false, no canary order has been placed (0 of 3), and nothing routes
> to the venue autonomously — but "no code can send an order" is **no longer** the reason. Section C
> is now safety-critical, LP5.3 above all.
Keep it current — when a milestone ships, tick its box or delete it here in the same PR.

Authoritative detail for each item lives in the linked roadmap docs; this file is the index.

---

## In-flight PRs

**None** (verified 2026-07-25 15:40 UTC — zero open PRs). Everything below reflects merged `main`
at `42ce85b`. The LP5 wave drained the same day: #183 (LP5.1), #186 (LP5.2), #187 (LP5.4), alongside
#184 (LP4 increment 2b + the governance flip).

One PR was **closed unmerged**: **#175** — a second LP4 order adapter written in a worktree without
re-checking `main`, which by then already carried `live_execution.py` (increment 1) with its reviewed
design record. Two adapters must not coexist: "which code can send an order" is exactly the question
this repo keeps unambiguous, so the one with the design record stayed. Its still-useful pieces are
catalogued in the PR body; the branch is `worktree-crypto-lp4-adapter`.

> Check live state with `gh pr list` rather than trusting this line — parallel machines open and
> merge PRs continuously.

---

## A. Prediction-market trading (Kalshi / Polymarket) — not started

Roadmap: [`docs/PREDICTION_MARKET_ROADMAP_V0.1.md`](PREDICTION_MARKET_ROADMAP_V0.1.md) (on `main`).
**No code exists for this track yet** — every box below is open.
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

Roadmap: `docs/LLM_ORCHESTRATION_ROADMAP_V0.1.md` (on `main`).
**This track is code-complete.** The only open line is M5b, which is an operator action, not a build.

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
- [ ] **M5b** Thomas promotes useful correction candidates to VALIDATED — **standing operator habit,
      not a build item.** The door already exists (`/memory` to list, `/promote` to approve, or
      `scripts/promote_memory_candidate.py`); nothing here is waiting on code. It stays open on
      purpose: M5a captures corrections as unverified `[M#]` candidates and M5c only feeds back what
      was promoted, so the loop produces nothing until someone says yes. That gate is the feature —
      it is what keeps a bad correction from entrenching itself as standing guidance.
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

Decision record: `docs/runtime-contracts/LIVE_EXECUTION_GOVERNANCE_V0.1.md` (decided 2026-07-23;
**the governance packet is now implemented except the LP4-coupled flag** — that doc's own step
table is the authority). Status: `docs/runtime-contracts/CRYPTO_LIVE_EXECUTION_V0.1.md`.

The `feat/cost-budget-ledger` dependency this section once recorded is **void** — that branch was
never pushed and the sequencing was deliberately reversed (2026-07-24); the two claim different
scopes at different levels, so nothing was owed to it.

- [x] **Governance implementation** — steps 1, 2, 4, 5, 8, 9 done (PR #142): `permission_decision.v0.4`
      adds `FINANCIAL_APPROVED_TRADING_USE`; the scope is in `policy_dispositions.EXECUTE_AND_REPORT`;
      `p5_policy_gate` is defined; `permission.py` builds a live-order decision at P5; the v0.4
      validator + positive example exist; both replay bundles regenerated.
  - [x] Step 3 — `financial_transaction_execution_implemented: false → true` — **flipped 2026-07-25**
        with LP4 increment 2b (PR #184), i.e. only once LP4 could actually send. It moved in lockstep
        with `ORDER_PATH_IMPLEMENTED = True` and the readiness board, asserted to agree
        (`CRYPTO_LIVE_EXECUTION_VERIFICATION_V0.1.md`). `financial_executor_enabled` stays `false`
        and untouched. **Read this as a posture change, not a checkbox:** an order path now exists,
        so READY on the readiness board means a real order can be placed on that machine.
  - [x] New closed schema `live_trading_budget.v0.1` (registered trading caps, self-hashed) —
        done 2026-07-25 (schema + `live_budget.py` + `register_live_trading_budget.py`).
  - [x] Step 6b: the guard reads the registered budget as authoritative (over env caps) — done
        2026-07-25 (`resolve_live_order_limits` + `budget_registered` guard check + the readiness
        `registered_budget` row). No live order without a valid registered budget. Grants nothing.
  - [x] New narrow role `execution.live_trader` — P5, `external_action_allowed: true`, **candidate
        (non-routable)** — done 2026-07-25 (contract + index-only registry entry + hash; passes
        contract-consistency + release gate). Grants nothing; **activating** it (candidate →
        routable) is the separate remaining `ROLE_GOVERNANCE` approval.
  - [x] Validator assertions + the v0.4 positive example + **both replay bundles regenerated** —
        done with steps 8/9 (PR #142). Any future policy edit changes its SHA-256 and owes the
        bundles another rebuild (CRLF-normalized; `rebuild_bundle` has no CLI entrypoint).
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
- [~] **LP5** position kernel + cycle routing — design record `LP5_POSITION_KERNEL_DESIGN_V0.1.md`.
      **Three of four increments have landed; only LP5.3 is left, and it is the one that changes the
      safety posture.**
  - [x] **LP5.1 — position state + reconciliation** (PR #183, 2026-07-25):
        `crypto/live_position.py`. Live positions live in their own `live_positions/` namespace with
        `stage: "live"` (paper keys on `(venue, symbol, timeframe)` with the same `binance_futures`
        venue string, so a shared book would let the paper cycle settle a *real* position). The venue,
        not the store, is the truth: `reconcile_positions` returns RECONCILED / DRIFT /
        ACCOUNT_UNREADABLE, and on anything but RECONCILED entries are refused while **closes stay
        allowed** — being unable to see the account must never trap an open position. Concurrency
        caps: 2 open live positions, 1 per symbol.
  - [x] **LP5.1c — the one fail-open closed** (same PR): `evaluate_live_order_guard`'s
        `current_open_notional_usdt=0.0` default asserted "the account is flat" on no evidence. The
        argument is now **required**, and unknown exposure is reported *at the cap*
        (`compute_open_notional_usdt(None, at_cap=…)`), so not knowing blocks instead of permitting.
  - [x] **LP5.2 — sizing** (PR #186): `crypto/live_sizing.py`. `min(risk-based, budget cap)`, floored
        to the venue's lot step in integer space, then **re-checked** after flooring; a size that
        cannot satisfy both bounds is refused, never defaulted. Risk per trade is 1% of usable
        (available-balance) equity.
  - [x] **LP5.4 — the outcome bridge** (PR #187): a live outcome now carries `result_R`,
        `risk_usdt`, `created_at_utc` and strategy lineage (`candidate_id` /
        `strategy_rule_hash` / `strategy_generation_id`), so `guards.run_risk_guard`, `lifecycle`
        and the C6 feedback report read live results with **no live-specific branch** — a live loss
        streak can finally demote a strategy the way a paper one does. The load-bearing part is the
        **exclusion rule**: `guards._closed_rows` reads a missing `result_R` as `0.0`, i.e. a
        *breakeven*, so an R-less live loss would have **shortened** a loss streak. Rows whose risk
        was never recorded are excluded as `LIVE_OUTCOME_NO_RECORDED_RISK` rather than given a
        fabricated R, and stay fully visible to the daily-loss breaker (which needs no R).
        `live_analysis_summary` reports readable and excluded counts separately so live trades never
        silently re-define a previously reported paper expectancy.
  - [ ] **LP5.3 — the live leg + cycle routing** ⚠️ **needs its own explicit Thomas decision before
        anyone starts it.** This is no longer ordinary plumbing: with the order path implemented and
        step 3 flipped, cycle routing is precisely the piece that makes an **autonomous** live order
        structurally reachable — today the only door is the deliberate
        `scripts/place_canary_order.py`, one canary at a time. It also owes two things the design
        record already names: cancelling the surviving bracket leg (the venue documents no
        auto-cancel), and feeding the readiness board the *real* open exposure so the honest
        block-at-cap above can lift.
- [ ] **≥ 3 clean canary orders** before any autonomous run (currently **0**; 1 existed in the frozen
      source system and did not migrate). **Operator-only, real money** — `scripts/place_canary_order.py`
      on Thomas's machine with his own keys and its own confirmation phrase
      (`MVP_LIVE_CANARY_CONFIRMATION`, deliberately distinct from the live-trading phrase so neither
      authorizes the other's capability). Claude does not run it.
- [x] The **symbol-starved router** finding — closed 2026-07-25 (PR #148). A crypto schedule with an
      empty request now fans out over every `(symbol, timeframe)` the pool routes on **plus** every
      context holding an open paper position (`cycle.run_pool_cycle`), and `route_entries` matches
      the whole `symbol_scope` rather than `symbol_scope[0]`, booking under the traded symbol. A
      named `SYMBOL [TIMEFRAME]` request is still a single-context operator override.
- [x] ⚠️ **Decided 2026-07-25 (Thomas): option (a)** — the C4 risk guard reads **this runtime's own
      outcomes only**; `run_lifecycle` keeps the full history. Found while correcting Gate 0:
      `import_crypto_history.py` deliberately routes the predecessor's closed outcomes into the
      store *"the C4 risk guard and C6 feedback read"*, and the measured effect was 112 imported
      rows worth **+266.8R** inside the rolling week — so the weekly-loss breaker could not trip
      however this runtime performed. Transient (those rows age out within days) but the cause was
      not, so the cause was fixed: `cycle.run_crypto_cycle` now passes
      `split_by_provenance(outcomes)[0]` to the guard. Lifecycle was left alone deliberately —
      imported outcomes carry strategy lineage, and promotion/demotion is a performance judgement,
      not a safety brake; a test pins the two call sites as scoped differently. The import script
      itself is untouched.

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
