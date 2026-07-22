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

## Candidate shadow-validation path (increment 3; explicit Thomas decision 2026-07-22)

`transition_candidate` / `record_shadow_result` + the CLI's `ready` / `validate` /
`shadow` / `accept` / `reject` commands:

- **Forward-only lifecycle:** DRAFT → REVIEW_READY → VALIDATING (shadow → RUNNING) →
  shadow PASS/FAIL recorded → ACCEPTED or REJECTED. ACCEPTED/REJECTED are terminal;
  `reject` is allowed from any pre-terminal status.
- **The runtime never runs the shadow.** Programs are unregistered and
  `unregistered_or_disabled_resource_execution` is BLOCK, so the shadow/limited comparison
  (policy §5) is performed by Thomas; the runtime enforces its evidence discipline: an
  outcome can only be recorded while the candidate is VALIDATING with the shadow RUNNING
  (started by `validate` — an outcome cannot appear from nowhere), it requires a non-empty
  `comparison_ref` + `result`, and it is single-shot (no re-recording; a wrong outcome is
  a new decision, not an edit).
- **`accept` requires shadow PASS** (`ACCEPT_REQUIRES_SHADOW_PASS`) — acceptance by
  assertion is impossible; a FAILed shadow can only be rejected.
- **Acceptance grants nothing.** `activation_eligibility` and `permission_expansion` are
  closed-schema constants, so an ACCEPTED candidate is a review milestone, not a
  capability: registry and activation stay APPROVAL_REQUIRED and unreachable from here.
- Same discipline as every review action: operator identity + reason required,
  kill-switch bound, each action a tamper-evident `programization_review_event.v0` (with
  the shadow status the decision was made against) on the programization ledger stream;
  candidate rows are append-only latest-wins, schema-validated before persisting.

## Threshold amendment (2026-07-22, explicit Thomas decision): 10 → 5

`REVIEW_TRIGGER_COUNT` is 5. Contract change done as a version bump, not an edit:
`programization_pattern.v0.2` (`review_trigger_count: const 5`) and
`programization_candidate.v0.2` (`valid_repetition_count: minimum 5`); v0.1 records
(threshold 10) stay valid history and the runtime migrates a v0.1 row forward on its next
touch (next valid observation / next operator transition). The governance key
`memory_learning.ten_valid_repetitions_result` keeps its historical name (value —
review-trigger-only semantics — unchanged); renaming it would ripple the operating-policy
schema for a label.

## Program request (increment 4; explicit Thomas decision 2026-07-22)

`runtime/mvp_runtime/program_request.py` + the CLI's `request` command: the chain's next
link — **invocation evidence, never invocation** (`program_request.v0.1`, an ACTIVE
record contract; creation is ALLOW per `tool_or_program_request_creation`).

- Requires an **ACCEPTED** candidate; one request per candidate.
- Every field computed from real state: the registry snapshot comes through the canonical
  resolver (`runtime/registry_resolution.py`, definition-hash checked, fail-closed);
  task lineage anchors to the pattern's last valid observation's REAL task (the
  promotion-audit precedent); the refused invocation binds a real BLOCK
  PermissionDecision; budget is honestly zero (no program-call budget exists).
- While the registry has no active Programs the verdict is always fail-closed
  **BLOCK** (`program_not_registered` / `program_not_active_and_enabled` +
  `runtime_implementation_unavailable`, allowlists empty, budget zero) — the schema's
  `runtime_effect` constants pin the record REVIEW_ONLY/false throughout.
- **Permission gate widening (narrow):** BLOCK decisions are now *buildable* solely as
  resource-refusal evidence for `UNREGISTERED_RESOURCE_EXECUTION` /
  `DISABLED_RESOURCE_EXECUTION` (`_BLOCK_EVIDENCE_SCOPES`;
  `build_resource_refusal_permission_decision` is the only door) — the Program Request
  contract requires the refused invocation to reference its refusing decision. A BLOCK
  record performs nothing and every other BLOCK scope stays unbuildable. For refusal
  evidence an insufficient authority is *recorded* (schema:
  `authority_sufficient: false ⇒ BLOCK`), not raised.
- Audited as `programization_review_event.v0` (`program_request_created`, carrying the
  BLOCK verdict + reasons) on the programization ledger stream.

## Registry registration (increment 5; explicit Thomas decision 2026-07-22)

`runtime/mvp_runtime/registration.py` + `scripts/register_program_candidate.py` — the
C8b verified-never-spent pattern applied to the registry:

- **Ask** (`--request`): requires the full lineage (ACCEPTED candidate **and** its
  program request; the program id/version come from the request, never from arguments).
  A real bound task anchors an APPROVAL_REQUIRED PermissionDecision — scope
  `TOOL_PROGRAM_GOVERNANCE` joined `_APPROVAL_REQUIRED_SCOPES` (explicit Thomas decision
  2026-07-22), P4, no consumption implementation — whose fingerprint binds the canonical
  hash of the exact definition content; `approval.build_approval_request` turns it into
  the PENDING R9 ask Thomas answers with `/approve` / `/reject`.
- **Definition content:** Thomas authors `purpose` / `inputs` / `outputs` in an input
  file; the runtime pins everything load-bearing — `status: candidate`,
  `enabled: false`, `implementation_available: false`, effects all false, permission
  level from the program request. One hash is both the approval's content binding and
  the registry's `definition_sha256`.
- **Apply** (`--approval-id … --confirm`): kill-switch first; the approval is *verified*
  (APPROVED, unexpired, right action type, content hash re-derived from CURRENT lineage
  + input — anything changed since Thomas's yes refuses) and never consumed; then the
  definition file + registry entry are written **into the working tree**, self-checked
  through the canonical resolver (a failed check removes the created file and leaves the
  registry untouched), and recorded on the programization ledger stream
  (`program_registered`). Committing the change is Thomas's PR — the runtime never
  touches `main`. Create-only: existing id+version or definition path refuses. No
  without-approval escape exists on this door.
- **Registration grants nothing:** the entry is `candidate` + `enabled: false` +
  no implementation, so `unregistered_or_disabled_resource_execution: BLOCK` still
  refuses it at every chokepoint.

## Next (separate Thomas decisions)

Program **activation** (`status: active`, `enabled: true`, a runtime implementation, and
role/assignment allowlists) is its own explicit approval — not reachable from any door
built so far.
