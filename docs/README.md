# Thomas Autonomous Organization Document Map

**Status:** `MVP Document Structure v0.5 Read-only Runtime Candidate`
**Owner:** `Thomas`

## 1. Document Structure

```text
Thomas
└─ Thomas Core
   └─ Operating Constitution
      Target document; inactive in MVP
      └─ Organization Architecture
         └─ MVP Operating Policy
            └─ Runtime Contract Precedence Addendum
               └─ Canonical Runtime Contracts
                  └─ Prime and Dynamic Role Contracts
                     └─ Role, Program, and Tool Registries
                        └─ Runtime Records and Audit Events
```

Organization Architecture defines structure and long-term boundaries.

MVP Operating Policy defines active operating rules.

Canonical Runtime contracts define machine-facing semantics and record structures.

## 2. Active MVP Documents

| Level | Document | MVP Use |
| --- | --- | --- |
| Current Core Release | `../THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` | Created only after verified Approval and committed Activation; absent by design before activation |
| Thomas Core | `../THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | Review-ready Active Rule projection; Runtime activation still depends on the Current Core Release pointer |
| Organization Architecture | `thomas-autonomous-organization-architecture-v0.1.md` | Target, MVP, and Dynamic Team architecture |
| Operating Policy | `MVP_OPERATING_POLICY.md` | Execution, risk, permission, Telegram, Memory, failure, learning, and Audit rules |
| Runtime Precedence | `runtime-contracts/RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.4.md` | Resolves migration conflicts and identifies canonical contracts |
| Core Release Lifecycle | `runtime-contracts/CORE_RELEASE_LIFECYCLE_V0.3.md` | Self-contained Release snapshots, Approval authority, Activation, rollback, Revocation, deactivation, and Current pointer |
| Core Context Binding | `runtime-contracts/CORE_CONTEXT_BINDING_V0.3.md` | Exact Core Release, hashes, approval, Rule subset, inheritance, and rebind lineage for one Task revision |
| Programization Review | `runtime-contracts/PROGRAMIZATION_REVIEW_POLICY_V0.1.md` | Defines valid repetition and the review boundary before Program Candidate creation |
| Task | `runtime-contracts/TASK_CONTRACT_V0.3.md` | Canonical work unit, Core Binding reference, scope, classification, authority, Permission, routing, validation, budget, lifecycle, result, and Audit |
| Task State Machine | `runtime-contracts/TASK_STATE_MACHINE_V0.1.yaml` | Canonical state transitions and guards |
| Authority and Permission | `runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md` | Separates P0–P6 from ALLOW–BLOCK |
| Permission Decision | `runtime-contracts/PERMISSION_DECISION_CONTRACT_V0.3.md` | Exact action, Authority chain, risk, decision, Approval binding, and Review-only guards |
| Action Approval | `runtime-contracts/APPROVAL_CONTRACT_V0.1.md` | Thomas-verified action-bound Approval lifecycle without executor authority |
| Action Fingerprint | `runtime-contracts/ACTION_FINGERPRINT_POLICY_V0.1.md` | Deterministic exact-action SHA-256 binding and secret exclusion |
| Thomas Permission/Approval Principles | `runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md` | Human-readable Thomas-approved bounded-autonomy policy |
| Thomas Permission/Approval Policy | `runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` | Machine-readable decision matrix, Control Channel, TTL, GitHub, financial, validation, and Kill Switch policy |
| Agent Output | `runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md` | Assignment and Role lineage for Agent results |
| Execution Budget | `runtime-contracts/EXECUTION_BUDGET_SCHEMA.yaml` | Canonical numeric Task, Role, Assignment, and usage budget |
| Prime Charter | `../03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md` | Thomas Prime identity, responsibility, authority, and prohibitions |
| Dynamic Role Contract | `../03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` | Dynamic Role common rules |
| Role Definition Template | `../03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml` | Machine-readable Role Definition structure |
| Role Assignment | `../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` | Task-specific Role scope, authority, Permission, Memory, resources, validation, and budget |
| Role Registry | `../03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` | Role status, routability, version, hashes, and Candidate trial policy |
| Program Registry | `../05_REGISTRIES/PROGRAM_REGISTRY.yaml` | Registered Program status and Runtime availability |
| Tool Registry | `../05_REGISTRIES/TOOL_REGISTRY.yaml` | Registered Tool status and Runtime availability |
| Resource Request Boundary | `runtime-contracts/RESOURCE_REQUEST_REVIEW_ONLY_BOUNDARY_V0.1.md` | Shared fail-closed and non-execution boundary |
| Tool Request | `runtime-contracts/TOOL_REQUEST_CONTRACT_V0.1.md` | Exact Tool request lineage, Registry, allowlist, Authority, Permission, budget, and review evidence |
| Program Request | `runtime-contracts/PROGRAM_REQUEST_CONTRACT_V0.1.md` | Exact deterministic Program invocation request and review evidence |
| Execution / Validation / Audit Boundary | `runtime-contracts/EXECUTION_VALIDATION_AUDIT_REVIEW_ONLY_BOUNDARY_V0.1.md` | No-Executor, no-side-effect, validation-only, append-only boundary |
| Execution Request | `runtime-contracts/EXECUTION_REQUEST_CONTRACT_V0.1.md` | Exact upstream, Authority, Permission, Approval, idempotency, budget, and preview plan binding |
| Execution Result | `runtime-contracts/EXECUTION_RESULT_CONTRACT_V0.1.md` | No-execution, blocked, expired, superseded, or preview evidence only |
| Validation Result | `runtime-contracts/VALIDATION_RESULT_CONTRACT_V0.1.md` | Independent or automatic review without Permission grant or subject mutation |
| Audit Event | `runtime-contracts/AUDIT_EVENT_CONTRACT_V0.1.md` | Append-only hash-bound evidence with no hidden Runtime effect |
| Executor Registry Design | `runtime-contracts/EXECUTOR_REGISTRY_CONTRACT_V0.1.md` | Empty non-Runtime design Registry; no active Executors |
| Executor Readiness Review | `runtime-contracts/EXECUTOR_READINESS_REVIEW_CONTRACT_V0.1.md` | Explicit prerequisite review without readiness grant |
| Disabled Restricted Execution Service | `runtime-contracts/DISABLED_RESTRICTED_EXECUTION_SERVICE_INTERFACE_V0.1.md` | Fail-closed evidence-only interface with no adapters |
| Hot-Path Revalidation | `runtime-contracts/HOT_PATH_PRE_EXECUTION_REVALIDATION_CONTRACT_V0.1.md` | Immediate pre-execution checks without token or handoff |
| Approval Consumption Preview | `runtime-contracts/APPROVAL_CONSUMPTION_CONTRACT_V0.1.md` | One-time Approval eligibility preview without state mutation |
| Rollback / Recovery | `runtime-contracts/ROLLBACK_RECOVERY_CONTRACT_V0.1.md` | Checkpointed restoration and recovery plan evidence only |
| Monitoring Snapshot | `runtime-contracts/MONITORING_SNAPSHOT_CONTRACT_V0.1.md` | Offline metric evidence; no daemon or live readiness claim |
| Alert Event | `runtime-contracts/ALERT_EVENT_CONTRACT_V0.1.md` | Alert evidence without Telegram, email, webhook, or external delivery |
| Health Snapshot | `runtime-contracts/HEALTH_SNAPSHOT_CONTRACT_V0.1.md` | Health evidence without restart or automatic remediation |
| Clock Sync Evidence | `runtime-contracts/CLOCK_SYNC_EVIDENCE_CONTRACT_V0.1.md` | Recorded offset evidence without NTP or system-clock mutation |
| Kill Switch State | `runtime-contracts/KILL_SWITCH_STATE_CONTRACT_V0.1.md` | Review-only unbound state and command vocabulary |
| Kill Switch Command Review | `runtime-contracts/KILL_SWITCH_COMMAND_REVIEW_CONTRACT_V0.1.md` | Command review without dispatch or Runtime state change |
| Executor Candidate Intake | `runtime-contracts/EXECUTOR_CANDIDATE_INTAKE_CONTRACT_V0.1.md` | Candidate proposal intake without Registry mutation |
| Executor Candidate Intake Review | `runtime-contracts/EXECUTOR_CANDIDATE_INTAKE_REVIEW_CONTRACT_V0.1.md` | Review-backlog decision without activation or handoff |
| Control Channel Identity Binding | `runtime-contracts/CONTROL_CHANNEL_IDENTITY_BINDING_CONTRACT_V0.1.md` | Metadata-only private Telegram binding review; no connection or verification |
| Control Channel Command Envelope Review | `runtime-contracts/CONTROL_CHANNEL_COMMAND_ENVELOPE_REVIEW_CONTRACT_V0.1.md` | Non-dispatched command review bound to identity fingerprints |
| Disabled Process Supervisor | `runtime-contracts/DISABLED_PROCESS_SUPERVISOR_INTERFACE_V0.1.md` | Static interface evidence without process observation or control |
| Disabled Scheduler | `runtime-contracts/DISABLED_SCHEDULER_INTERFACE_V0.1.md` | Schedule plan without installation, enablement, dispatch, or Task creation |
| Monitoring / Alert Threshold Policy | `runtime-contracts/MONITORING_ALERT_THRESHOLD_POLICY_V0.1.md` | Conservative Review draft; not Runtime active |
| Threshold Evaluation | `runtime-contracts/MONITORING_ALERT_THRESHOLD_EVALUATION_CONTRACT_V0.1.md` | Offline classification without alert delivery or remediation |
| Local Reversible Sandbox Test Plan | `runtime-contracts/LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_PLAN_V0.1.md` | Not-run isolated candidate plan with escape, secret, network, and subprocess denial |
| Local Reversible Sandbox Test Review | `runtime-contracts/LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_REVIEW_CONTRACT_V0.1.md` | NOT_RUN_NOT_READY review; no test authorization or activation |
| I0.4 Contract Set Index | `runtime-contracts/I0_4_RUNTIME_CONTRACT_SET_INDEX_V0.1.md` | Canonical frozen I0.4 review-only contract inventory |
| I0.4 Consolidation Checkpoint | `runtime-contracts/I0_4_CONSOLIDATION_CHECKPOINT_V0.1.md` | Deduplication decisions, freeze rule, Repository entry gate, and I0.5 boundary |
| I0.4 Consolidation Boundary | `runtime-contracts/I0_4_CONSOLIDATION_REVIEW_ONLY_BOUNDARY_V0.1.md` | Explicit no-Runtime-effect boundary |
| I0.5 Read-only Runtime Input Bundle | `runtime-contracts/READ_ONLY_RUNTIME_INPUT_BUNDLE_CONTRACT_V0.1.md` | Exact hash-bound development replay inputs |
| I0.5 Read-only Runtime Kernel | `runtime-contracts/READ_ONLY_RUNTIME_KERNEL_CONTRACT_V0.1.md` | Deterministic in-memory orchestration candidate |
| I0.5 Deterministic Worker | `runtime-contracts/DETERMINISTIC_CONTRACT_INSPECTION_WORKER_CONTRACT_V0.1.md` | No-model/no-Tool/no-Program contract inspection Worker |
| I0.5 Read-only Runtime Run | `runtime-contracts/READ_ONLY_RUNTIME_RUN_CONTRACT_V0.1.md` | Completed or blocked development replay evidence |
| I0.5 Read-only Runtime Boundary | `runtime-contracts/I0_5_READ_ONLY_RUNTIME_BOUNDARY_V0.1.md` | Explicit no-authoritative-Runtime and no-side-effect boundary |

## 3. Machine-Readable Schemas

```text
schemas/
├── task.v0.3.schema.json
├── core_context_binding.v0.3.schema.json
├── role_assignment.v0.2.schema.json
├── agent_output.v0.2.schema.json
├── execution_budget.v0.1.schema.json
├── permission_decision.v0.3.schema.json
├── approval.v0.1.schema.json
├── thomas_permission_approval_operating_policy.v0.1.schema.json
├── tool_request.v0.1.schema.json
├── program_request.v0.1.schema.json
├── execution_request.v0.1.schema.json
├── execution_result.v0.1.schema.json
├── validation_result.v0.1.schema.json
├── audit_event.v0.1.schema.json
├── executor_registry.v0.1.schema.json
├── executor_readiness_review.v0.1.schema.json
├── disabled_executor_evidence.v0.1.schema.json
├── pre_execution_revalidation.v0.1.schema.json
├── approval_consumption_preview.v0.1.schema.json
├── rollback_recovery_plan.v0.1.schema.json
├── monitoring_snapshot.v0.1.schema.json
├── alert_event.v0.1.schema.json
├── health_snapshot.v0.1.schema.json
├── clock_sync_evidence.v0.1.schema.json
├── kill_switch_state.v0.1.schema.json
├── kill_switch_command_review.v0.1.schema.json
├── executor_candidate_intake.v0.1.schema.json
├── executor_candidate_intake_review.v0.1.schema.json
├── control_channel_identity_binding.v0.1.schema.json
├── control_channel_command_envelope_review.v0.1.schema.json
├── process_supervisor_snapshot.v0.1.schema.json
├── scheduler_plan_review.v0.1.schema.json
├── monitoring_alert_threshold_policy.v0.1.schema.json
├── monitoring_alert_threshold_evaluation.v0.1.schema.json
├── local_reversible_sandbox_candidate_test_plan.v0.1.schema.json
├── local_reversible_sandbox_candidate_test_review.v0.1.schema.json
├── i0_4_runtime_contract_set_index.v0.1.schema.json
├── read_only_runtime_input_bundle.v0.1.schema.json
├── read_only_runtime_run.v0.1.schema.json
├── thomas_core_release_manifest.v0.3.schema.json
├── thomas_core_release_approval.v0.3.schema.json
├── core_activation.v0.1.schema.json
├── core_deactivation.v0.1.schema.json
├── core_revocation.v0.1.schema.json
├── current_core_release.v0.2.schema.json
├── programization_observation.v0.1.schema.json
├── programization_pattern.v0.1.schema.json
├── programization_candidate.v0.1.schema.json
└── operational_knowledge.v0.1.schema.json
```

The Task schema validates structure and selected state-specific invariants.

The Task State Machine validates transition direction and transition guards.

Both are required.

## 4. Legacy Integrated Contract Document

`thomas-twin-core-architecture-v0.1.md` remains useful as architecture and historical I/O reference.

It is not a source of truth for new Runtime records. Its Task v0.2, Agent Output v0.1, legacy Permission enums, legacy risk vocabulary, and L0-L5 autonomy examples are historical only.

For new Runtime records, the Runtime Precedence Addendum and canonical contracts listed above supersede conflicting shorthand or older schema sections.

Existing records remain valid under their original schema versions.

## 5. I0.4 Consolidation and I0.5 Entry

I0.4.2-I0.4.7 is now a frozen Review-only contract foundation. The canonical inventory is `05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml`.

Before I0.5:

- apply the cumulative bundle to the real Repository;
- pass all focused validators and the consolidated contract-set validator;
- pass Contract/Schema parity and Static Integrity;
- pass the full Repository Gate;
- generate a new Source Fingerprint, Gate evidence, and self-contained Review Release;
- review the new Release ID and hashes.

Next implementation phase:

- I0.5 Read-only Runtime Kernel.

Deferred until later approved stages:

- Tool/Program writes, Executor handoff, Approval consumption, Control Channel dispatch, process control, Scheduler dispatch, Sandbox writes, external execution, and financial execution.

Implemented or planned documents do not grant Runtime permission.

## 6. Change Rule

- Core Release changes require a new immutable Manifest under `THOMAS_CORE/releases/<release_id>/`.
- Core Runtime activation requires a separate approval record and `CURRENT_CORE_RELEASE.yaml`.
- A running Task does not silently rebind to a newer Core Release.
- Core approval and Core Binding do not grant execution Permission.

- Thomas Core and Operating Constitution changes require Thomas approval.
- Active Core promotion always requires explicit Thomas approval, versioned update, and Audit.
- Runtime Contract changes require version updates and compatibility review.
- Role Definition changes require Registry consistency and hash updates.
- Material Task changes increment Task revision and supersede affected Runtime records.
- Any Gate-owned source change invalidates prior Gate evidence for a new Release build. Rerun the Repository Gate and build a new Review Release before Thomas Approval.
- Any Permission/Approval foundation source change requires the same Gate refresh; no prior Review Release may represent the modified source.
- Runtime Records and Audit Events are append-only; corrections are new events rather than silent overwrite.


## Review-Only Learning and Programization Records

```text
programization_observation.v0.1
↓
programization_pattern.v0.1
↓
programization_candidate.v0.1
```

Ten independent valid repetitions trigger Review only.

A Program Candidate remains pending Program Registry and Permission Policy.

Validated Operational Knowledge includes review due dates, environment signatures, confidence, and stale/deprecated states.

## 6. I0.5 Read-only Runtime Candidate

Implemented:

- hash-bound development replay Input Bundle;
- Repository-root read boundary;
- Task/Core/Role/Assignment/Registry preflight;
- Authority and Permission evaluation;
- deterministic in-process Worker with zero model, Tool, Program, network, and filesystem-write calls;
- Agent Output, automatic Contract Validation, Audit chain, unchanged source Task snapshot, and separate `REPLAY_COMPLETED` lifecycle;
- completed and blocked Run records;
- focused Validator, CLI Self-Test, and 34 fail-closed mutation fixtures.

Still disabled:

- Runtime-authoritative mode;
- Tool/Program execution;
- model-provider invocation;
- filesystem write;
- external or financial action;
- Approval consumption;
- Executor handoff;
- Scheduler or Control Channel dispatch;
- Permission/Authority expansion;
- Core activation.

The I0.5 Candidate must be applied to the real Repository and pass the real focused and Full Repository Gates before any later Runtime-authoritative design is considered.
