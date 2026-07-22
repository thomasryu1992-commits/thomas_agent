# Candidate Role Trial v0.1

**Status:** Active runtime capability (gated OFF by default)
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the canonical Governance
Policy (`governance/GOVERNANCE_POLICY.yaml`), the MVP Dynamic Role Contract §14, and the
Role Registry own the rules it obeys.

The runtime's first way to *exercise* a non-activated role. The registry carries five
candidate roles that normal routing can never select (`status: candidate`,
`routable: false`); the Candidate Trial Policy has always said how one may run anyway —
explicit Thomas approval, exact role version, an explicit `candidate_trial` assignment,
isolation, a numeric budget, independent validation, and a full audit record. This
increment makes that policy executable out of existing parts, for the first two
candidates Thomas put on trial: `research.general` and `translation.general`
(decision 2026-07-22; the mechanism itself is role-generic).

Implemented in `runtime/mvp_runtime/trial.py` + `trial_cli.py`
(+ `planner.select_candidate_role`, `permission.build_trial_permission_decision` /
`build_trial_work_permission_decision`, `audit.build_trial_consumption_audit`, and the
`assignment_mode="candidate_trial"` path of `assignment.build_role_assignment`).

## Zero new contracts — the governance already modelled the trial

- `role_tool_program.candidate_role_trial: APPROVAL_REQUIRED` and the
  `CANDIDATE_ROLE_TRIAL` scope (TTL max 30m) were already in the canonical policy and in
  the closed `permission_decision.v0.3` / `approval.v0.2` enums.
- `role_assignment.v0.2` already defines `assignment_mode: candidate_trial` and already
  **requires** `trial_authorization_ref` in that mode (a schema conditional).
- The R9 approval flow (ask → verified `/approve` on the control channel) and the R10
  consumption pattern (single-use spend behind the `approval_consumption` safety flag)
  are reused unchanged in shape.

The one runtime-gate widening: `permission._APPROVAL_REQUIRED_SCOPES` now admits
`CANDIDATE_ROLE_TRIAL` beside `SENSITIVE_MEMORY_GOVERNANCE` — an explicit Thomas decision
(2026-07-22), exactly the kind of widening CLAUDE.md reserves to him. Every other
APPROVAL_REQUIRED scope stays refused.

## The flow

```
trial_cli request <role_id> "<trial task text>"
    -> select_candidate_role (candidate-only; active roles refused; hash-verified)
    -> CANDIDATE_ROLE_TRIAL PermissionDecision (APPROVAL_REQUIRED, ORANGE, P3)
       fingerprint binds: role_id + role_version + definition_sha256 + trial task text
    -> approval.v0.2 request, stored + audited (APPROVAL_REQUESTED)

Thomas: /approve <id> [reason]   (verified Telegram private channel — R4 identity gate)

trial_cli run <approval_id>
    -> kill-switch check; approval status/expiry/fingerprint/scope checks
    -> hot-path revalidation: the CURRENT registry entry must still be the exact
       candidate approved (same version, same definition bytes, same task text
       -> CONTENT_CHANGED refused; an activated role refuses ROLE_ALREADY_ACTIVE)
    -> plan the isolated run (records only; a plan that cannot build costs nothing)
    -> Safety-Flag Gate: the same approval_consumption flag as R10 (env opt-in + local
       integrity-checked activation; an env var alone runs nothing)
    -> SPEND FIRST: compare-and-set under the consume lock, CONSUMED appended before the
       model runs — a partial failure leaves the grant spent-but-unrun (ask again)
    -> the isolated run: candidate role worker (its own prompt + its own declared
       role_specific_output keys) -> automatic validation (role output keys required)
       -> independent validator ALWAYS reviews (skipped only on an automatic BLOCK)
       -> stricter verdict decides -> candidate_trial_report.v0 + full audit trail
```

## Isolation (structural, not advisory)

The `candidate_trial` assignment closes the memory scope entirely (no readable scopes, no
candidate creation — `assignment.build_role_assignment` enforces it, so
`build_memory_candidates` returns nothing); there is no search PermissionDecision and no
tool use; no workspace write; both allowlists stay empty (candidates declare none). The
worker runs at P2 ANALYZE under an ALLOW `INTERNAL_ANALYSIS` decision
(`internal.analysis.candidate_trial`) within the candidate's P3 ceiling.

## What a trial can never do

- **Activate or promote the role.** The report records `promotion_effect: NONE`; the
  registry is never written. Promotion remains a separate Thomas approval + registry
  version update (§14: "Candidate trial permission does not activate or promote").
- **Escape its binding.** The approval fingerprints the exact role version, definition
  hash, and task text; any drift refuses the spend. One grant = one run
  (`ALREADY_CONSUMED` forever after, even when the run itself failed).
- **Run ungated.** Kill switch first; then the `approval_consumption` safety flag —
  deleting the local activation record is a live revocation.

## Records and audit

Per run, appended to the per-machine ledger: the planned task, both work
PermissionDecisions (specialist + validator), the `candidate_trial` assignment (+
validator assignment), agent output, both validations, budget usage, and the
`candidate_trial_report.v0` (self-hashed; role id/version/definition hash, approval id,
verdicts, isolation attestation). The audit trail chains the trial consumption event
(`APPROVAL_CONSUMED` / `CANDIDATE_ROLE_TRIAL` / `NO_ACTIVATION`) followed by the standard
pipeline events; a post-spend failure chains the consumption event + a BLOCKED trail.
Persistence failures surface as `persist_error`, never as a silent success.

## Deliberately excluded

Trial series/repetition (each run is one grant), trial-driven auto-promotion, trials of
active roles, live search or memory context inside a trial, and any widening of the
consumable scopes beyond `SENSITIVE_MEMORY_GOVERNANCE` + `CANDIDATE_ROLE_TRIAL` — each a
separate explicit Thomas decision.
