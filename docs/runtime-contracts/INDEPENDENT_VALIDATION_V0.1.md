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
```
