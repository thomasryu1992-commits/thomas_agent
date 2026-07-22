# Programization Counter v0.1 — runtime wiring of the repetition trigger

**Status:** Implemented (runtime increment; the governing contracts stay authoritative)
**Governing contracts:** `PROGRAMIZATION_REVIEW_POLICY_V0.1`, `PROGRAMIZATION_RUNTIME_RECORDS_V0.1`,
`programization_observation.v0.1` / `programization_pattern.v0.1` schemas,
`GOVERNANCE_POLICY.yaml` (`memory_learning.ten_valid_repetitions_result`, `role_tool_program`)
**Code:** `runtime/mvp_runtime/programization.py` (+ pipeline/audit/store/CLI wiring)

## What this is

The runtime leg of the Programization Review Policy: each COMPLETED+PASS run may record one
`programization_observation.v0.1` and fold it into its pattern's
`programization_pattern.v0.1` counter. **Ten valid independent observations raise a Review
opportunity only** — the pattern flips to `TRIGGERED` exactly once and a
`PROGRAMIZATION_REVIEW_TRIGGERED` audit event (a type `audit_event.v0.1/v0.2` already
declared) joins that run's hash chain, asserting `REVIEW_ONLY` / `NO_PROGRAM_CREATED`.

Zero new contracts / schemas / registries / gates: the policy, the record schemas, the
audit event type, and the governance dispositions all pre-existed; this increment only made
the first two records real.

## What this is NOT

- **Not program creation.** `programization_candidate.v0.1` is the *review's* outcome,
  decided by Thomas — out of scope here (`ten_valid_repetitions_result:
  PROGRAMIZATION_REVIEW_TRIGGER_ONLY`).
- **Not activation.** `tool_or_program_activation` stays APPROVAL_REQUIRED,
  `unregistered_or_disabled_resource_execution` stays BLOCK, every assignment still carries
  `allowed_program_ids: []`.
- **Not a permission change.** Programization never expands Permission (policy §8).

## Semantics

- **Opt-in store** (`ProgramizationStore`, default
  `.runtime_governance_state/programization/` — local, gitignored machine state). No store,
  no observation, run stays pure. The intake CLI, operator loop, and scheduler wire the
  default store; tests inject temp stores.
- **Pattern signature** (policy §2): locked MVP task type + assignment role id + content
  hashes of the actual input/output schema files + a hash of the pipeline stages the run
  actually executed (a run with the independent validator or controlled write is a
  materially different process → different pattern) + the worker version as environment.
- **Valid repetition** (policy §3/§4), fail-closed in the not-counting direction:
  - `retry_of_same_task_revision` — same task id + revision seen before (store history);
  - `duplicate_replay` — same trace id seen before;
  - `same_input_without_independent_business_event` — byte-identical request seen before
    (the runtime cannot verify an independent business event, so a repeat never counts);
  - `synthetic_test` — the run's provider has no network egress (an in-process mock);
  - `validation_revision_cycle` / `incomplete_task` — structurally excluded (R7 has no
    revision cycles; only COMPLETED+PASS runs are observed);
  - `fixture` / `manual_smoke_test` — not detectable at this seam; stated limitation.
- **Trigger once:** `NOT_TRIGGERED` → `TRIGGERED` on the observation that reaches the
  threshold; `TRIGGERED`/`UNDER_REVIEW`/`CLOSED` are never overwritten by the counter
  (those transitions belong to the operator review).
- **Concurrency:** the whole read–count–append is one cross-process file-lock critical
  section, so concurrent runs cannot double-count or both claim the trigger.
- **Fail-closed records, best-effort seam:** both records are validated against their
  closed schemas before persisting; a corrupt store or invalid record refuses the
  observation (`PROGRAMIZATION_UNREADABLE` / `PROGRAMIZATION_RECORD_INVALID` /
  `OBSERVATION_INCOMPLETE`) — but the counter is enrichment, so the pipeline notes
  `programization_error` on the result and still delivers the run (the working-memory
  accumulation precedent).
- **Durability:** the observation + updated pattern ride the run's records ledger
  (`programization_observation` / `programization_pattern` kinds); the store keeps
  append-only JSONL with latest-wins pattern rows (the working-memory `find_candidate`
  precedent). Observation rows carry a store-internal `input_sha256` sidecar the closed
  schema deliberately does not (needed to detect replayed input).

## Review handling (increment 2; explicit Thomas decision 2026-07-22)

`runtime/mvp_runtime/programization_cli.py` + `transition_review` / `create_program_candidate`:

- **Operator transitions, forward-only:** `review` moves TRIGGERED → UNDER_REVIEW, `close`
  moves TRIGGERED/UNDER_REVIEW → CLOSED. The counter owns NOT_TRIGGERED/TRIGGERED, the
  operator owns UNDER_REVIEW/CLOSED, and CLOSED is terminal (reopening is a new decision).
  The counter keeps counting during and after a review without ever touching an
  operator-owned status. Every mutation requires an operator identity + reason and is
  refused otherwise.
- **Candidate creation** (`candidate` command): the review's outcome per policy §5, allowed
  only while UNDER_REVIEW, one per pattern. Thomas authors the substance in an input file
  (`deterministic_slice`, `agent_retained_responsibilities`, `defined_exceptions`,
  `rollback_procedure_ref`, optional metrics); the runtime contributes identity, the
  pattern's count, `shadow_validation: NOT_STARTED`, and the schema's hard constants —
  `activation_eligibility: candidate_only_pending_program_registry_and_permission_policy`
  and `permission_expansion: false`. A candidate grants **nothing**
  (`candidate_status_does_not_grant_runtime_permission`); creation itself is ALLOW-tier
  (`tool_or_program_request_creation: ALLOW`). Secret-bearing input and schema-invalid
  records fail closed before anything persists.
- **Audit:** each transition / draft appends a tamper-evident
  `programization_review_event.v0` (`stamped_event`, the memory-retention precedent) to its
  own ledger stream (`programization_events.jsonl`) — operator decisions about accumulated
  state, anchored to no single task.
- **Kill-switch bound:** `status` answers while PAUSED/KILLED (read-only door); `review` /
  `candidate` / `close` are refused — the memory-prune door rule.

## Next (separate Thomas decisions)

Candidate lifecycle beyond DRAFT (REVIEW_READY/VALIDATING/ACCEPTED/REJECTED, shadow
validation), program request records, registry entries, and any activation are each their
own explicit approval — none is reachable from this CLI.
