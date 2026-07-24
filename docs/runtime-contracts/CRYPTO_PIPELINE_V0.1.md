# Crypto Pipeline v0.1 (C1 — design contract)

**Status:** Draft — approved design direction, implementation phased C2+
**Owner:** Thomas
**Authority:** None. This document describes a planned implementation; the canonical
Governance Policy (`governance/GOVERNANCE_POLICY.yaml`) owns every rule it obeys.

Absorbs the functional behavior of the standalone `crypto_AI_System` project
(data collection → strategy research → entry-condition validation → paper trading →
feedback) into this runtime's governance structure. The source project's five "agents"
become **governed pipeline stages inside one Task**; its ad-hoc safety rules become
this repo's enforced chokepoints; its strategy-generation lifecycle becomes a
candidate → approval → promotion flow.

This is a port by **effect-tier reclassification**, not by directory copy. Each
behavior enters at its correct permission tier behind an existing chokepoint.

## Relationship to the organization architecture

This is the first domain application of the Dynamic Task Team Architecture
(`docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md`):

- **One pipeline cycle = one Task = one Dynamic Task Team** assembled by Thomas Prime.
- The five deterministic stages are **Programs** in the architecture's vocabulary
  (§8.4: "Rule-based Task → Program"), not Agents. No new role contracts.
- The **independent audit/feedback agent** the architecture prescribes is the existing
  `validation.independent` (R7), applied to cycle output under `--independent-validation
  auto` semantics unchanged.
- Agent separation stays governed by §13 (3+ of 6 criteria). The expected first
  trigger: when strategy evaluation gains an LLM judgment step, activate the
  dormant `research.general` candidate role. Not now.
- Long-term (§15): a paper track record with revenue evidence makes this domain the
  first Business Group candidate. Out of scope here; the path exists in the source doc.

## Effect-tier mapping (the core of the design)

| crypto_AI_System behavior | Effect | This runtime's expression (precedent) |
|---|---|---|
| Market data collection (exchange public REST) | External read | INTERNAL_READ · ALLOW behind per-provider safety flags (`binance_market_data`, `coinalyze_market_data`); backend failure → `MARKET_DATA_DEGRADED`, degrades — never blocks (R3 `SEARCH_DEGRADED`) |
| Feature build + strategy rule evaluation + entry decision | Internal compute | INTERNAL_ANALYSIS · ALLOW, audited (R7.2 triage precedent). Deterministic — no model call, no gate |
| Data health + risk guard (`allow_new_position`) | Internal validation | Automatic checks merged stricter-wins with any independent validation (R7) |
| Paper position open/close (state mutation) | Internal reversible write | EXECUTE_AND_REPORT behind a `paper_trading` safety flag; DryRun default; kill-switch bound (R8 `filesystem_write` / `tool_write` precedent) |
| Performance report + delivery | Write + channel | R8 workspace write + R4 Telegram channel, unchanged |
| Recurring cycle (15 min) / factory (daily) | Scheduled execution | New R6 templates `crypto_pipeline`, `crypto_factory` — `kill_blocks: scheduler_execution` applies automatically |
| Strategy candidate generation (feedback, factory, rule miner) | Internal record creation | ALLOW-tier candidate creation (R5 working-memory precedent); never mutates the active pool |
| Strategy promotion (candidate → active pool) | Governed state change | APPROVAL_REQUIRED via R9; **consumption requires widening R10's scope — a separate explicit Thomas decision, deferred** (see below) |
| Testnet / live order submission | External + financial | **Superseded — see `CRYPTO_LIVE_EXECUTION_V0.1.md`.** Still not implemented, but for different reasons than stated here (below) |

## Stage design

One scheduled or operator-requested cycle runs as a single Task through the existing
pipeline machinery (intake → Prime plan → permission decisions → execution → audit):

```
data (Program)  →  research (Program)  →  validation (checks)  →  paper-trade (gated write)  →  feedback (Program)
```

Fail-closed semantics carry over from the source system and get stronger here:

- Upstream stage ERROR → paper-trade stage SKIPPED (audited), feedback still runs.
- Validation no-trade verdict → cycle continues in no-new-position mode (DEGRADED).
- Each stage that differs in effect gets **its own PermissionDecision** — the
  independence the source system approximated with process separation is expressed
  as permission separation.
- Kill switch: PAUSED/KILLED refuses the cycle at the scheduler door and the
  paper-write door with the standard mode-aware refusals (`RUNTIME_PAUSED`/`RUNTIME_KILLED`).

## Strategy lifecycle (generation fusion)

The source system already models strategies as **declarative, versioned, content-hashed
data** (`strategy_spec.v1`: entry/exit rules over named features; `generation_id`
lineage; per-record sha256). That makes the lifecycle a record flow, not a code flow:

```
feedback / factory emits candidate spec (ALLOW, audited, lineage = parent strategy ids)
  → backtest evidence attached (INTERNAL_ANALYSIS, evidence refs)
  → approval request (R9: fingerprint over the spec content, Telegram /approve, TTL, single-use)
  → promotion consumes the approval (R10 pattern) → active pool pointer updated
  → superseded generation retired, never deleted (lineage preserved in the audit chain)
```

Rules this repo already enforces apply verbatim: a good backtest is **never**
auto-promotion (`auto_promotion_allowed: false` in the source data becomes structural
here); the active pool is a single pointer changed only through the approval door
(the Core Release pointer precedent).

**Deferred decision (explicit, Thomas-only):** R10 consumption is currently scoped to
`SENSITIVE_MEMORY_GOVERNANCE`. Strategy promotion would be the **second consumption
scope**. Until that decision, promotion candidates + approval requests can exist
end-to-end, but the final promotion remains an explicit operator action (the pre-R10
posture, which R9 already supports).

## Data migration (one-time audited import)

The accumulated paper evidence migrates well — it is already shaped like this repo's
records (self-hashed, versioned, id-chained):

| Source artifact | Records | Destination |
|---|---|---|
| `storage/registries/outcome_feedback_registry.jsonl` | 103 closed paper outcomes, per-record sha256 | `.runtime_governance_state/crypto/outcome_registry.jsonl` (append-only, ledger-lock pattern) |
| `storage/registries/counterfactual_outcome_registry.jsonl` | 52 | same store, kind-tagged |
| `storage/latest/active_strategy_pool.json` (+ specs, `GEN-*` lineage) | active pool | imported as **candidates** with provenance; the initial active pool is re-established through the approval door, not silently carried over |
| `storage/logs/event_log.jsonl` | history | **not imported** — stays with the frozen source repo as historical evidence |

Import rules:

- One-time, scripted, **audited as an import event** (reason-coded, counts + source
  hashes recorded); each imported record keeps its original id + sha256 and gains
  `provenance: crypto_ai_system_import` — pre-migration evidence is never
  indistinguishable from records this runtime produced.
- Import is idempotent (re-run detects existing provenance-marked records) and
  read-only toward the source repo.
- After the final cutover phase, the source repo is frozen (its own
  `archive/pre-lean-2026-07-15` precedent) and its Windows scheduled task disabled —
  **one runner only**, per the source repo's own storage-divergence rule.

## Zero-new-surface audit (C1 verdict)

| Need | Verdict |
|---|---|
| New role contracts | **0** — stages are Programs; validation reuses `validation.independent` |
| New gates | **0** — Safety-Flag Gate + kill switch + release gate cover every door |
| New safety-flag provider ids | data providers + `paper_trading` — these are **records, not schema**; `scripts/activate_safety_flag.py` already mints them |
| New scheduler templates | 2 (`crypto_pipeline`, `crypto_factory`) — code additions to R6, no schema change |
| New closed schemas | **0 now.** Outcome/strategy records keep the source system's own versioned shapes (`strategy_spec.v1`, `step296_outcome_analytics_v2`), carried as validated payloads inside existing audit/evidence envelopes. Formalizing `strategy_record` as a closed schema lands with the promotion-scope decision, where the approval fingerprint needs a canonical shape |
| New permission scopes | reuse `INTERNAL_READ` / `INTERNAL_ANALYSIS` / `WORKSPACE_REVERSIBLE_WRITE`-tier dispositions; the paper-state write reuses the EXECUTE_AND_REPORT widening R8 already made |

## Open decisions

1. **Dependency policy (blocks C3).** The source research/data modules use
   pandas/numpy. Recommendation: **pure-Python rewrite** of feature computation
   (ma/atr/adx over OHLCV lists) — the strategy specs are declarative, evaluation is
   simple arithmetic, and `requirements-runtime.txt` stays minimal (YAML + jsonschema
   + requests only). Fallback if C3 measurement shows this is impractical: an explicit
   Thomas decision to admit pandas/numpy into the runtime image.
2. **R10 second consumption scope** (strategy promotion) — deferred explicit decision,
   see above.
3. **Cutover timing** — at C7 E2E, decide fresh-start vs. imported history as the
   statistical baseline (import preserves both; the choice is which the risk guard reads).

## Phase roadmap

| Phase | Delivers | Gate condition |
|---|---|---|
| C1 | this contract | design locked, zero-new-surface verdict recorded |
| C2 | `runtime/mvp_runtime/crypto/` package + market-data collectors behind safety flags, DEGRADED semantics | mock backend E2E, no live flag |
| C3 | feature build + rule-evaluation engine (pure Python), strategy spec loader | replay parity against source-system fixtures |
| C4 | data-health + risk-guard checks wired into validation, stricter-wins | no-trade verdict degrades, never blocks |
| C5 | paper position kernel behind `paper_trading` flag (DryRun default) + outcome records | R8-pattern gate tests |
| C6 | feedback analytics + performance report + Telegram delivery | report rides existing R8/R4 paths |
| C7 | scheduler templates + one-time data import + E2E; source repo frozen | full release gate green, single-runner cutover |
| C8 | factory/rule-miner candidate generation + R9 approval requests for promotion | promotion stays operator-executed until the R10-scope decision |

Live/testnet order paths, the x10 SDK, streamlit dashboards, and the source repo's
backtesting UI are explicitly out of scope for every phase above.

## Amendment (2026-07-23): the live-order row is superseded

The effect-tier row above named the deferred `execution_request.v0.1` as the prerequisite for
live orders. **That was wrong**, and the correction matters because it was load-bearing for a
year of "not yet": that schema pins every execution field to `const: false`
(`execution_mode: PREVIEW_ONLY`, `financial_execution_allowed: false`,
`executor_ready: false`), so it is structurally incapable of *expressing* an order, let alone
authorizing one. It is a review-only artifact of the deferred Executor architecture.

The correct expression is the one R8 (controlled write) and R10 (approval consumption)
established afterwards: a PermissionDecision, a per-machine safety-flag grant, a DryRun
default, kill-switch binding, and an audit trail. Neither of those needed an Executor, and
neither needed `execution_request`.

What actually blocks a live order is narrower and is now written down: the runtime has no
permission scope it may legitimately use for a trade, no actor that can hold P5, and no place
to register a trading budget. Those decisions are recorded in
`LIVE_EXECUTION_GOVERNANCE_V0.1.md` and are not yet implemented.

The read and refusal legs **have** been ported (LP1–LP3, LP6) — see
`CRYPTO_LIVE_EXECUTION_V0.1.md`. The explicit financial-effect decision this row demanded was
taken on 2026-07-23; the implementation of it was not.
