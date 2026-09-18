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

### The live tier (#610 Part 1, 2026-08-09)

Pool membership and permission to spend real money are **two facts, not one**. Every entry
carries `live_tier`:

| tier | occupies a slot | papers / counterfactual | may open a REAL position |
|---|---|---|---|
| `OBSERVATION` (default, and the value of an absent or unrecognised field) | yes | yes | **no** |
| `LIVE` | yes | yes | yes |

- `pool.live_routable_strategy_ids` is the set `live_entry` refuses against, and it is strictly
  narrower than `routable_strategy_ids` — the wider one still answers "could this trade again
  at all", which the drawdown baseline needs and which an observation-tier lineage answers YES
  to. Conflating them would release a drawdown exclusion.
- **Only the operator promotion door arms.** It is a field rather than a status precisely
  because the lifecycle ladder recovers `WARNING → PAPER_ACTIVE`: a tier carried in `status`
  would be re-granted by a demotion path. The status write (`pool.apply_status_decisions`, which
  `update_statuses` wraps) writes only `status` and the `lifecycle_*` fields, so the ladder
  structurally cannot arm a strategy; tests assert the writer never names the field and that a
  recovery leaves an OBSERVATION entry's tier untouched. Two other
  writers exist and neither can arm: `pool.disarm_live_tier` (the automatic demotion and the
  operator's `scripts/disarm_live_strategies.py`) takes no target tier, and the history import's
  `--activate-pool` installs every entry at OBSERVATION whatever its file says (2026-09-16, PR1c
  review — it used to install the file's own tier, which armed with no approval and no stage).
- The tier is part of `promotion_content_sha256` (`PROMOTION_HASH_VERSION`, `strategy_promotion.v5`).
  An approval granted for an observation-tier install is not spendable on a live one, or the
  reverse. Since 2026-09-16 (PR1c) arming also needs Thomas's approval every time (no
  `--without-approval`, no `--allow-unconfirmed-holdout`) and an execution stage that admits a
  live entry.
- Absence means OBSERVATION, so every entry promoted before this shipped stopped being
  live-routable the moment it did. That is the intended migration and the fail-closed direction.
- Closes are unaffected: the tier door sits in the entry block, after settle and protect, so a
  position can always be closed by the strategy that opened it.

This does **not** weaken the rule above it: entry to the `LIVE` tier is still an explicit,
approval-bound operator action, and nothing automatic may grant it.

### The strategy artifact (PR3a, 2026-09-18, Thomas decisions 31-34)

A strategy exists as a candidate row, inside a promotion approval, and as the pool entry the
router reads. The promotion door copies the row onto the entry. Until PR3a nothing proved the copy
was what Thomas approved: the approval named ids and rule hashes as two separately sorted lists,
and the rule hash covers the spec's behavioural subset and nothing the router reads beside it.

`crypto/strategy_artifact.py` hashes what a strategy **is**, once (`strategy_artifact.v1`):

| part | from the row | on the entry |
|---|---|---|
| identity: `strategy_instance_id` (= `candidate_id`, decision 32), generation, rule-hash label | the row | top level |
| the spec, as the rule fingerprint its rule hash is computed over (parsed, so a re-serialized spec is the same spec) | `strategy_spec` | `strategy_spec` |
| admission: regime evidence, distribution reference | `admission_evidence(row)` | top level, where the doors read them |
| ranking: `champion_score` | the row | top level |
| cost basis: the cost model the backtest charged | `backtest_evidence.cost_summary.cost_model` | `strategy_artifact.cost_basis` |
| risk assumptions: the backtest doors' parameters (entry-cost cap, liquidation leverage and margin, cooldown) | `backtest_evidence` | `strategy_artifact.risk_assumptions` |
| evidence: candle-window hash, whole-evidence hash, closed count, win rate | the row | `strategy_artifact.evidence` |

- **Not in it:** the display id (the door renames on a collision, and the strategy is the same
  under either name, decision 31), and the fields the pool's other writers own (`status`, the
  `lifecycle_*` fields, `live_tier*`, `promoted_*`).
- **The door stamps** each new entry with the hash (`strategy_artifact_sha256`). The row's hash and
  the built entry's must be one (`STRATEGY_ARTIFACT_DIVERGED`); when an approval authorizes the
  install, the approval's content hash must also match the rows the door copies
  (`APPROVAL_CONTENT_MISMATCH`). `--without-approval` (OBSERVATION only) has no approval to match.
  The ledger event records the pairs (`promoted_artifacts`) and the tier.
- **The approval binds the pairs** (`strategy_promotion.v5`): `artifacts: [[candidate_id,
  artifact_sha256], ...]`, in the content hash and in the signed parameters. A row appended under
  the same candidate id after the ask mints another artifact, and the approval does not verify.
  A v4 approval verifies nothing.
- **Every pool read and install recomputes every stamp** from the entry itself; one entry that no
  longer hashes to its stamp refuses the whole pool (`STRATEGY_POOL_ARTIFACT_MISMATCH`, decision
  34), as a spec that does not parse does. The cycle then routes nothing and records the code.
  A writer that touches a hashed field would refuse the pool on the next read; the pool's writers
  today touch none, and tests pin that.
- **What the stamp proves.** It catches drift — a writer, a restore or a code path that changes
  what the router reads on a stamped entry. It is an unkeyed hash, so it does not authenticate:
  whoever can write the pool file can recompute it, or strip it and leave an entry that papers
  unbound. What authenticates money is the approval: a LIVE arm must be paired with its artifact by
  the approval Thomas answered, and an edited or stripped entry is not. The fields outside the hash
  that the live path reads (`live_tier`, `live_tier_approval_id`, `promoted_at`, the disarm trace)
  stay as hand-editable as before; an edit to them can now re-arm only an artifact a v5 approval
  pairs.
- **The format is frozen.** Every read recomputes the stamps on disk with the code deployed then,
  so a change to the v1 format moves every stamp at once. The spec is therefore hashed as its rule
  fingerprint (every stored rule hash already pins it), never `StrategySpec.to_dict()`, which has no
  stability rule (#461 added `venue` to it). A golden test pins the v1 result. A later version must
  keep writing the v1 stamp beside its own: this code treats an unknown version as a stamp that does
  not hold, so a rollback would otherwise refuse the pool.
- **When the pool is refused.** The error names the entry (index, display id, candidate id).
  - Disarm first if it is armed: `scripts/disarm_live_strategies.py` reads past an artifact that no
    longer holds, because it can only narrow (`pool.read_pool_to_disarm`). The promotion door, the
    lifecycle and retirement refuse a refused pool, as they do a spec that does not parse.
  - Then repair the named entry: restore its fields from the candidate row the ledger's
    `promoted_artifacts` names, or remove its `strategy_artifact_sha256` and `strategy_artifact`,
    which leaves an unbound entry that papers and can never be armed until it is re-promoted.
- **An entry with no stamp predates the artifact** (all 121 on 2026-09-18). It papers as before and
  can never be armed LIVE (decision 33): `pool.live_arm_unsound` names it `unbound`, and the
  order-time check requires the approval to pair the entry's artifact with its candidate. A LIVE
  arm needs a re-promotion under a v5 approval; there is no migration tool.
- **The win rate stays inside the artifact.** The lifecycle reads a top-level `backtest_win_rate`,
  and giving it a value would switch on the win-rate probation rule, a separate decision.
- **The records name the artifact (PR3a-2).** `strategy_artifact_sha256` rides beside the three
  lineage fields (candidate, rule hash, generation) wherever they go: the router's match and primary
  (`primary_strategy_artifact_sha256`), the plan, the paper position, its open event and its
  outcome; the shadow book's bench and conflict sides, the shadow position and its outcome; the
  forward book's position and outcome; the live intent, the pre-order snapshot's lineage, the live
  position and every live outcome (runtime exit, venue-closed settle, naked close).
  - None means no artifact named the strategy: an entry with no stamp, a position opened before
    PR3a-2 and settled after, or a probe or testnet order, which are not pool-routed. A record
    written before PR3a-2 has no field. So None is not evidence of an unstamped entry.
  - The pre-order gate binds it (`INTENT_BOUND_FIELDS`, `pre_order_intent.v2`) and refuses an
    autonomous order whose lineage names none, and `verify_live_arm` requires the armed entry's
    artifact to be the one the plan was made from (`PRE_ORDER_RISK_SNAPSHOT_V0.1.md` §2-3).
  - The live-trades board (`live_promotion`) prints it beside the rule hash.
  - Nothing keys on it: the realized ranking, the lifecycle and the allowances group by the
    lineage key (candidate, else generation and rule, else display id), below.

### Runtime keys are lineages (PR3b, Thomas decisions 35-38)

`strategy_id` is a display name: the factory restarts it every generation and the promotion door
renames on a collision. A runtime key that is the display id lets a lineage that reuses the id
inherit another's record, and a renamed lineage lose its own. The key is
`candidate_identity.outcome_attribution_key`: `cand:<candidate_id>`, else
`gen:<generation>:<rule hash>`, else `sid:<display id>`. The router and the lifecycle read all three
for a pool entry (`entry_attribution_keys`). The one imprecise join, `sid:`, reaches only rows that
name no candidate and no generation and rule hash: minted lineages write `cand:` rows, and an entry
installed without a candidate id writes `gen:` rows (or `sid:` rows if it names no generation).

- **Realized ranking and direction conflicts** (decision 35, PR3b-1): the cycle summarizes its own
  paper rows and the supporting-shadow settlements by lineage (`feedback.realized_by_lineage`, the
  report's `by_strategy` arithmetic, keeping each group's unrounded sum). The router reads an
  entry's record under every key it accepts and combines the groups from their sums into one mean,
  rounded once, as the report rounds it: the tiers compare that mean exactly, and a mean of rounded
  group means could turn an exact break-even into a proven edge. Ties go to the lineage key
  (`cand:` before `gen:` before `sid:`), then the display id. A resolved conflict's `basis` names the
  lineage.
- **Supporting shadows** (decision 38, PR3b-1): one open supporting shadow per context and lineage,
  both when the cycle opens one and in the book's dedupe door.
- **The live allowance** (decision 38, PR3b-1): an armed display id with no pool entry to name its
  lineage falls back to `sid:<display id>`; it was `id:`, which no outcome key equals. The fallback is
  unreachable from the cycle today, which hands the pool read its armed set came from. The allowance
  reads an entry's own key, not all three: live outcomes carry a candidate id since 2026-07-26, and
  only stamped entries can be armed.
- **Lifecycle decisions** (decision 36, PR3b-2): each decision names what it judged, the lineage
  (`candidate_identity.lineage_of`: candidate, generation, rule hash) and the status, and so does
  an operator retirement. The locked pool write (`pool.apply_status_decisions`) finds a decision
  stale when its display id names another lineage or another status by then, or no entry at all,
  or when it does not say what it judged (the keys are absent). It checks this after the shape
  guard and before the terminal guard.
  - The cycle's lifecycle skips a stale decision, reports it (`LIFECYCLE_DECISION_STALE`,
    `lifecycle_stale` on the cycle record, a marked report line) and applies the rest: a demotion
    held back for one stale decision would be the less safe outcome, and the next cycle judges the
    entry again.
  - An operator retirement is approved as a set and is all or nothing: it builds its decisions from
    the entries its approval was verified against, and a stale one refuses the whole retirement
    before anything is written. `update_statuses` is all or nothing too.
- Measured 2026-09-18, over the own paper and supporting-shadow outcomes and the occupying pool: no
  display id names two lineages, no lineage was recorded under two display ids, each occupying entry
  reads the same rows under both keys and none spans two keys, and no context has an exact score or
  realized tie. So PR3b-1 changed no routing decision there.

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
| `storage/latest/active_strategy_pool.json` (+ specs, `GEN-*` lineage) | active pool | imported as **candidates** with provenance; the initial active pool is re-established through the approval door, not silently carried over. `--activate-pool` installs at OBSERVATION only: arming is a separate approved decision |
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
