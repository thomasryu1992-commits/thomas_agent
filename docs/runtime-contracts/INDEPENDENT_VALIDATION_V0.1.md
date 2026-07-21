# Independent Validation Agent (R7) — v0.1

**Status:** Active (MVP runtime). **Normative authority:** None — the active
[`validation.independent`](../../03_ROLE_CONTRACTS/ROLES/ACTIVE/VALIDATION_ROLE.md) role
contract, `governance/GOVERNANCE_POLICY.yaml`, and `runtime/mvp_runtime/` remain
authoritative; this describes the runtime behavior.

R7 is the MVP's first multi-agent capability, deliberately scoped to the **minimum team the
governance already defines** (policy §3.4 / §6.1): the specialist plus an **independent
validation agent** — a second agent instance, in a fresh execution context, that reviews the
specialist's output and returns PASS / REVISE / BLOCK. Zero new contracts, schemas, roles, or
gates: the `validation.independent` role was already active + routable, and
`validation_result.v0.1` already models an independent role validator.

## What runs (opt-in)

Enabled per run with `--independent-validation` (one-shot CLI, operator loop). Default **off**
— every existing behavior is unchanged. This matches the role's activation condition
`prime_requests_validation_without_lowering_policy`: turning it on adds review, never removes any.

### R7.1/R7.2 — selective ("auto") policy and the validator's own provider

`--independent-validation auto` reviews **only when the task warrants it**, decided in
order:

1. **Classification** (`validation.independent_validation_required`): ORANGE/RED risk
   mandates the review (policy §3.4); an operator-marked important priority (HIGH/URGENT,
   via a leading `!중요` / `!important` token on the control channel — standalone token,
   stripped before intake — or `--important` on the one-shot CLI) requests it. Either way
   no triage is owed: the decision is made before any model call.
2. **Orchestrator triage (R7.2)** otherwise: Prime plans a governed triage action — its
   own `INTERNAL_ANALYSIS` P2 ALLOW PermissionDecision, built like the search and
   validation grants — and the pipeline spends one deliberately small model call (the
   request in, one label out; `TRIAGE_TOKEN_ALLOWANCE` on the task budget) on the
   validator/triage provider. A **HIGH** verdict runs the planned reviewer; **NORMAL**
   skips it. The verdict, its reason, and the call are recorded (`triage_result` /
   `triage_invocation` / `triage_permission_decision`) and audited as one `TRIAGE` trail
   event referencing the decision.

Under "auto" the two-agent team is always **planned** (planning is free and deterministic —
the R9 buildable-vs-executable precedent); the triage decides what **runs**, and the task
budget allocates the ceiling (2 agents + the triage call), with the actual spend in
`budget_usage`. Fail direction — **degraded, not blocked, and recorded**: a triage provider
failure or unusable verdict degrades to NORMAL with `TRIAGE_DEGRADED` on the audit chain;
the reviewer is an enhancement, so a broken triage must neither stop the analysis nor
silently double every run's spend. The bare flag (or `always`) keeps the R7
review-every-run behavior.

`MVP_VALIDATOR_PROVIDER` (e.g. `groq`, or a comma-separated failover chain) gives the
validator **its own gated provider**, so the review runs on a different free quota than the
analysis — and a different model family makes the second opinion more independent, not less.
Exactly the same Safety-Flag Gate rules as `MVP_HOSTED_PROVIDER`: every member needs its own
local grant, an unknown/unauthorized member fails the whole selection closed at startup, and
the env var alone opens nothing. Unset keeps the R7 pairing (mock validator for a mock
specialist, else the specialist's provider).

1. Prime plans the two-agent team: the validator gets its **own** PermissionDecision
   (`SIMULATION_VALIDATION` scope, P2 ANALYZE — a distinct ALLOW action) and its **own**
   `role_assignment.v0.2` (own actor id, own budget); the task routing records both agents.
2. The specialist runs as today; the deterministic automatic checks run as today.
3. The **validator agent** runs: one model call in a fresh context. Its prompt carries the
   goal, the original request, and the output under review — never the specialist's prompt,
   search context, or memory context (review starts from goal/input/result, per §3.4).
4. Verdict merge: the **stricter** of the automatic and independent results decides delivery
   (`stricter_rule_wins`). Only PASS delivers; REVISE/BLOCK withhold, with the validator's
   findings and actionable revision requests in the block reasons.
5. Everything is recorded and audited: the independent `validation_result.v0.1`
   (`validator_type: ROLE`, `validation_mode: INDEPENDENT`), the validator's model call
   (its own MODEL_INVOKED event), and a second VALIDATION_COMPLETED event in the hash chain.

## Independence (verified, not asserted)

`independence_verified` is computed, never assumed: the validator's actor instance,
assignment, and role must all differ from the output's creator. A validator assignment whose
role equals the creator's role fails closed (`NOT_INDEPENDENT`). The validator never modifies
the original output (`mutates_subject: false`) and its verdict grants nothing
(`permission_boundary` all false).

## Fail-closed directions

- Provider/transport failure → no review happened → the run BLOCKs (`PROVIDER_ERROR`).
- A response with no usable PASS/REVISE/BLOCK verdict → **BLOCK verdict** (per the role:
  insufficient evidence is a BLOCK, never a skipped validation or a silent PASS).
- The validator is skipped only when the automatic checks already BLOCK — the outcome is
  decided, so no model call is spent (§6.1 minimum-team economy).
- Budget: the validator enforces its own assignment budget (one model call, token cap,
  timeout) exactly like the specialist worker.

## Provider reuse (one gate governs both agents)

The validator asks the model for the same analysis-JSON shape the providers already parse;
the verdict rides in `recommendation.action`. No provider/network code changed, so the
Safety-Flag Gate chokepoints are untouched: with the mock provider the validator uses the
deterministic `MockValidatorProvider`; with a locally-activated real provider the validator
uses the same gated provider (second model call per run — the cost of the second opinion).

## Deliberately out of scope (over-design guards)

A revision cycle (feeding REVISE findings back to the specialist for a re-run), parallel
agents, agent fleets, dynamic team assembly beyond this pair, inter-agent messaging, and
activating any candidate role. REVISE reports the validator's actionable revision requests to
the operator; re-submitting is the operator's call.

## Key modules

- `runtime/mvp_runtime/validator.py` — `MockValidatorProvider`, `build_validator_prompt`,
  `run_validation_worker`, `stricter_result`.
- `runtime/mvp_runtime/prime.py` — plans the validator team (`independent_validation=True`).
- `runtime/mvp_runtime/permission.py` — `build_validation_permission_decision`
  (SIMULATION_VALIDATION, P2).
- `runtime/mvp_runtime/pipeline.py` — runs the validator after the automatic checks and
  merges the stricter result.

```bash
python -m runtime.mvp_runtime.cli --independent-validation "이 사업 아이디어를 분석해줘: ..."
python -m runtime.mvp_runtime.operator_cli --independent-validation

# R7.1: review only ORANGE/RED-risk tasks and operator-marked important requests,
# with the validator on its own provider quota:
MVP_VALIDATOR_PROVIDER=groq python -m runtime.mvp_runtime.operator_cli --independent-validation auto
python -m runtime.mvp_runtime.cli --independent-validation=auto --important "이 사업 아이디어를 분석해줘: ..."
```
