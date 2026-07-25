# LLM Orchestration Roadmap — v0.1

**Status:** planning record (not a contract). Captures the Thomas-requested build-out of the
general-request LLM pipeline and the crypto strategy-search pipeline. Each milestone is a
branch → PR → gates → merge; one capability per PR. Milestones that require an explicit
Thomas decision are marked ⚠️.

Author date: 2026-07-24.

## Why this exists

Thomas described a target behaviour the runtime does not yet fully implement:

1. A Telegram request is reviewed by an LLM.
2. The request's **difficulty (상/중/하)** selects an OpenRouter model of matching capability.
3. The chosen model finds/produces the content; a second LLM **verifies** it.
4. If accurate → deliver. If not → an LLM **revises** it, then re-verify.
5. Separately, on a schedule or on demand, an LLM helps find crypto strategies with good
   **win-rate + risk-reward (손익비)** over real data.

A code audit (2026-07-24) found the *skeleton* is built but two orchestration pieces and one
crypto selection detail are missing or differ from the description. This roadmap closes the gap
inside the repo's rules: **reuse first, fail-closed, gate every capability, name every
Thomas-decision point.**

## Current reality vs. the target (audit summary)

| Target behaviour | Today | Gap |
| --- | --- | --- |
| LLM reviews the request | Triage LLM judges the request (`triage.py`) | ✅ exists |
| Difficulty 상/중/하 | Triage returns only importance HIGH/NORMAL; `complexity` is hardcoded `NORMAL` (`planner.py`) | ⚠️ 2-level, not difficulty |
| Difficulty → OpenRouter model | Difficulty picks an `openrouter_light/standard/heavy` tier via `select_tiered_provider`, fail-closed to base (`providers.py`) | ✅ M2 (done) |
| Specialist LLM produces content | `run_analysis_worker` (`worker.py`) | ✅ exists |
| Verifier LLM | Independent validator (`validator.py`), own gated provider | ✅ exists |
| Accurate → deliver | PASS → rendered response (`pipeline.py`) | ✅ exists |
| Inaccurate → LLM revises → retry | FAIL → BLOCKED, handed back to human; **no auto-revision** (deliberately removed after a live "endless REVISE loop", `validator.py`) | ❌ missing (by past design) |
| Crypto strategy search on schedule/on-demand | 3 schedule kinds + CLI (`scheduler.py`) | ✅ exists |
| LLM finds strategies | LLM only **proposes** declarative families; deterministic code judges (`crypto/proposer.py`) | ⚠️ propose-only, manual CLI |
| Win-rate selection | Ranking is anti-overfit **robustness score**; win-rate is reporting/demotion only (`crypto/robustness.py`, `lifecycle.py`) | ⚠️ differs |
| Risk-reward (손익비) selection | Hard gate `target_atr/stop_atr ≥ 1.0` (`crypto/factory.py`), not a ranking term | ✅ as a gate |

## Milestones

### M0 — env cleanup + baseline *(done 2026-07-24, no code change)*

- `.env`: removed the duplicate `MVP_HOSTED_PROVIDER` key (the last line silently overwrote the
  first, hiding `google_ai_studio` from the chain). Now one line, full chain:
  `MVP_HOSTED_PROVIDER=openrouter,google_ai_studio,groq` — OpenRouter first (M2 tiering builds on
  it), google/groq as failover (tried only on 503/429).
- Verified all three provider grants exist and are unexpired (`safety_flag_activations/`).
- Known follow-up: `workspace.writer` grant expired 2026-07-18 → R8 `--write-output` is
  fail-closed until re-activated (`scripts/activate_safety_flag.py`).

### M1 — difficulty triage: HIGH/NORMAL → 상/중/하 *(1 PR, no Thomas decision)*

- Extend `triage.py` to **also** emit a `difficulty` tier (LOW/MEDIUM/HIGH) beside the existing
  importance verdict. Reuse the `_VERDICT_FOLD` pattern; unparseable/failed degrades to MEDIUM
  (the `TRIAGE_DEGRADED` precedent — never blocks).
- **Observe-only:** difficulty is recorded in `triage_result` but changes no routing yet. The
  existing importance verdict still decides whether the independent reviewer runs. Ship the
  classifier first, watch its quality on real traffic, then wire M2.
- Governance: reuses the existing `INTERNAL_ANALYSIS` P2 ALLOW triage action — **zero** new
  contracts / schemas / gates. `triage_result` is a free dict (no closed schema), so the field
  is additive.

### M2 — difficulty → OpenRouter tier model ⚠️ *(done 2026-07-24; Thomas approved tier ids + default slugs)*

- Tier provider ids `openrouter_light` / `openrouter_standard` / `openrouter_heavy`, each its
  OWN grant + own model-slug env (`MVP_OPENROUTER_MODEL_LIGHT/STANDARD/HEAVY`). The M1 difficulty
  (LOW/MEDIUM/HIGH) picks the tier via `providers.select_tiered_provider`; the tier gate opens
  through a new `safety_gate.select_gated_optional` — same "factory receives the Authorization"
  safety property as `select_gated`, but returning a degrade signal instead of raising.
- **Fail-closed:** if the chosen tier has no local grant → degrade to the base
  `MVP_HOSTED_PROVIDER` chain, recorded as `model_tier_selection` with `TIER_DEGRADED` (the
  SEARCH_DEGRADED precedent). An inert/mock base is left unchanged (nothing to upgrade), and a
  per-tier grant can never authorize another tier (a light grant does not open heavy).
- **Wiring / scope:** the tier serves the **specialist only** (the validator/triage keep their
  own provider). It is applied when the triage produced a difficulty — i.e. the R7.2 auto path
  (`--independent-validation auto`); on a run where no triage ran there is no difficulty and the
  base provider serves unchanged. Extending triage-to-every-request for universal tiering is a
  later cost decision (an extra triage call per run).
- **Thomas decision (taken):** tier ids as above; default slugs light =
  `openai/gpt-oss-20b:free`, standard = `meta-llama/llama-3.3-70b-instruct:free`, heavy =
  `deepseek/deepseek-r1:free` — fallbacks only, overridden per machine via the envs. Minting the
  per-tier grant with `activate_safety_flag.py` and setting the real slugs stays the local
  operator step; until then every run degrades cleanly to base.
- Governance: reuses the existing gate/provider machinery — **zero** new contracts / schemas /
  registries / gates.

### M3 — verify-fail → bounded LLM revision loop ⚠️ *(1 PR, Thomas decision required)*

- On an independent-validator FAIL, feed the validator's already-collected `next_actions`
  (`validator.py`) back into the specialist for **at most one** regeneration → re-verify → still
  FAIL ⇒ current BLOCKED + human hand-off.
- **Why the decision is mandatory:** an "endless REVISE ratchet" occurred live and the loop was
  removed on purpose. Re-introduction conditions:
  - hard cap of **1** retry, not configurable upward;
  - a **pre-allocated retry budget** (the `TRIAGE_TOKEN_ALLOWANCE` precedent) — over budget ⇒
    no loop, BLOCKED;
  - both retry and give-up audited (`REVISION_ATTEMPTED` / `REVISION_EXHAUSTED`).
- No new contract/schema — a second worker call inside the same run, reusing existing record shapes.

### M4 — crypto: align "win-rate + risk-reward" selection ⚠️ *(2 decisions, ~1 PR each)*

- **M4a (decision):** whether candidate ranking should reflect win-rate / expectancy.
  Recommended: keep robustness as the **first-pass** anti-overfit filter, then a **second-pass**
  sort by win-rate + risk-reward — consistent with `lifecycle.py`'s "never discarded on win rate
  alone." Alternative (adding a performance term into the robustness score) reintroduces the
  overfit risk the original design avoided.
- **M4b (decision):** whether to put the proposer on a schedule. This reverses the 2026-07-24
  "manual CLI only — proposals accumulate faster than anyone reviews" decision, so if scheduled
  it needs a per-run proposal cap + an unreviewed-backlog cap (skip + audit when exceeded).
- Unchanged and correct as-is: risk-reward ≥ 1.0 hard gate, human `/approve` promotion, live
  execution gated OFF at multiple layers.

### M5 — trial-and-error learning: correction → memory → next-run context ⚠️ *(multi-PR, Thomas decisions per increment)*

Thomas's ask: when a run comes out A→B and he corrects it B→C→D, record that path so a
*future* similar request goes A→D directly — i.e. the agent's work quality improves the way he
wants. The repo is built for exactly this ("every growth loop starts from recorded outcomes"),
and much of the substrate already ships:

- **Outcome signal** — Operator Feedback E1 (ACTIVE): `/feedback good|bad|<note>` binds one
  `operator_feedback.v0` event to the last delivered run (`operator_feedback.py`). But by its own
  contract *"nothing here reads feedback back into planning"* — E2 (evaluation) and E3
  (distillation into planning proposals) are unbuilt.
- **The memory of the correction** — R5 memory: a working-memory CANDIDATE (ALLOW) →
  Thomas promotes to VALIDATED (APPROVAL_REQUIRED, already implemented) → later runs retrieve it
  as a `[V#]` reliable-context block (`memory.retrieve_validated_memory`). This is the channel
  that carries "next time, do D."
- **Codifying a repeated success** — Programization: the *same* pattern seen 5× triggers a review
  that can become a deterministic Program (A→D turned into code). The far end of learning.

**What's missing** for the automatic A→D loop:
1. The correction *content* (the B→C→D deltas, or Thomas's `/feedback` note) is not captured as a
   memory candidate automatically — today it lives only in the ledger / his head.
2. Nothing feeds a past correction back into the *planner's* prompt for a similar new request
   (E3). Retrieval exists for VALIDATED memory; wiring corrections into it does not.

**Proposed increments (each its own Thomas decision):**
- **M5a** — at a revision/correction moment (M3's loop, or a `/feedback bad + note`), mint a
  working-memory CANDIDATE holding the delta ("for requests like X, prefer D over B"). ALLOW-tier,
  audited — the working-memory precedent, nothing auto-trusted.
- **M5b** — Thomas promotes the useful candidates to VALIDATED (existing APPROVAL_REQUIRED door).
  This is the "as I want" gate: only what he approves becomes standing guidance, so a bad
  correction can't entrench itself.
- **M5c** — the planner retrieves matching VALIDATED corrections as `[V#]` context for new
  requests, so the specialist starts closer to D. Same scope-gate / recency-cap / fail-closed
  semantics as today's validated-memory read-back.
- **M5d (later)** — repeated identical corrections flow into the programization counter → a
  candidate Program → (separate Thomas approval) a deterministic A→D.

By design this stays **operator-gated**: learning is real, but promotion is Thomas's explicit yes.
That gate is the feature, not friction — it is what makes it improve *the way he wants* rather
than drifting on every stray correction.

## Sequence

```
M0 (done) → M1 → M2 (⚠️ tier ids+slugs) → M3 (⚠️ revision loop) → M5a→M5b→M5c (⚠️ learning loop)
                                     └→ M4a / M4b (⚠️ crypto, parallel)
```

M5 builds naturally on M3 (the revision moment is where a correction is born), but M5c (feeding
VALIDATED corrections back to the planner) can ship independently on top of today's memory.

## Invariants every milestone keeps

- Reuse first; no new contract/schema/registry/gate unless an existing owner truly can't express it.
- Fail-closed: missing/uncertain/unauthorized → degrade-and-audit or BLOCK, never guess.
- Every model call is a governed action under its own PermissionDecision, budgeted and audited.
- Safety-Flag Gate unchanged: an env var alone never opens a network path; every provider/tier
  needs its own local grant.
