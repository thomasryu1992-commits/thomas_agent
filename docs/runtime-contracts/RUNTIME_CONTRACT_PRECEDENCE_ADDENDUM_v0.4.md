# Runtime Contract Precedence Addendum v0.4

**Status:** `Active MVP Addendum`
**Document Version:** `0.4.1`
**Owner:** `Thomas`

## 1. Purpose

This addendum identifies the canonical Runtime contracts while older integrated architecture and I/O documents are migrated.

## 2. Canonical Runtime Contracts

| Subject | Canonical Document |
| --- | --- |
| Core Release Lifecycle | `CORE_RELEASE_LIFECYCLE_V0.3.md` |
| Current approved Core Release | `../../THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` |
| Core Context Binding | `CORE_CONTEXT_BINDING_V0.3.md` |
| Programization Review | `PROGRAMIZATION_REVIEW_POLICY_V0.1.md` |
| Task | `TASK_CONTRACT_V0.3.md` |
| Task state transitions | `TASK_STATE_MACHINE_V0.1.yaml` |
| Authority levels and Permission Decisions | `AUTHORITY_AND_PERMISSION_MODEL.md` |
| Permission Decision | `PERMISSION_DECISION_CONTRACT_V0.3.md` |
| Action Approval | `APPROVAL_CONTRACT_V0.1.md` |
| Action Fingerprint | `ACTION_FINGERPRINT_POLICY_V0.1.md` |
| Thomas Permission/Approval Principles | `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md` |
| Thomas Permission/Approval Policy | `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` |
| Agent Output | `AGENT_OUTPUT_CONTRACT_V0.2.md` |
| Execution Budget | `EXECUTION_BUDGET_SCHEMA.yaml` |
| Dynamic Role common rules | `../../03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` |
| Role Assignment | `../../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` |
| Role Registry | `../../03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` |
| Program Registry | `../../05_REGISTRIES/PROGRAM_REGISTRY.yaml` |
| Tool Registry | `../../05_REGISTRIES/TOOL_REGISTRY.yaml` |
| Resource Request Boundary | `RESOURCE_REQUEST_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| Tool Request | `TOOL_REQUEST_CONTRACT_V0.1.md` |
| Program Request | `PROGRAM_REQUEST_CONTRACT_V0.1.md` |
| Execution / Validation / Audit Boundary | `EXECUTION_VALIDATION_AUDIT_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| Execution Request | `EXECUTION_REQUEST_CONTRACT_V0.1.md` |
| Execution Result | `EXECUTION_RESULT_CONTRACT_V0.1.md` |
| Validation Result | `VALIDATION_RESULT_CONTRACT_V0.1.md` |
| Audit Event | `AUDIT_EVENT_CONTRACT_V0.1.md` |
| Executor Registry Design | `EXECUTOR_REGISTRY_CONTRACT_V0.1.md` |
| Executor Readiness Review | `EXECUTOR_READINESS_REVIEW_CONTRACT_V0.1.md` |
| Disabled Restricted Execution Service | `DISABLED_RESTRICTED_EXECUTION_SERVICE_INTERFACE_V0.1.md` |
| Hot-Path Pre-Execution Revalidation | `HOT_PATH_PRE_EXECUTION_REVALIDATION_CONTRACT_V0.1.md` |
| Approval Consumption Preview | `APPROVAL_CONSUMPTION_CONTRACT_V0.1.md` |
| Rollback and Recovery Plan | `ROLLBACK_RECOVERY_CONTRACT_V0.1.md` |
| Executor Foundation Boundary | `EXECUTOR_FOUNDATION_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| Monitoring Snapshot | `MONITORING_SNAPSHOT_CONTRACT_V0.1.md` |
| Alert Event | `ALERT_EVENT_CONTRACT_V0.1.md` |
| Health Snapshot | `HEALTH_SNAPSHOT_CONTRACT_V0.1.md` |
| Clock Sync Evidence | `CLOCK_SYNC_EVIDENCE_CONTRACT_V0.1.md` |
| Kill Switch State | `KILL_SWITCH_STATE_CONTRACT_V0.1.md` |
| Kill Switch Command Review | `KILL_SWITCH_COMMAND_REVIEW_CONTRACT_V0.1.md` |
| Executor Candidate Intake | `EXECUTOR_CANDIDATE_INTAKE_CONTRACT_V0.1.md` |
| Executor Candidate Intake Review | `EXECUTOR_CANDIDATE_INTAKE_REVIEW_CONTRACT_V0.1.md` |
| Operations Evidence / Intake Boundary | `OPERATIONS_EVIDENCE_EXECUTOR_INTAKE_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| Control Channel Identity Binding | `CONTROL_CHANNEL_IDENTITY_BINDING_CONTRACT_V0.1.md` |
| Control Channel Command Envelope Review | `CONTROL_CHANNEL_COMMAND_ENVELOPE_REVIEW_CONTRACT_V0.1.md` |
| Disabled Process Supervisor Interface | `DISABLED_PROCESS_SUPERVISOR_INTERFACE_V0.1.md` |
| Disabled Scheduler Interface | `DISABLED_SCHEDULER_INTERFACE_V0.1.md` |
| Monitoring / Alert Threshold Policy | `MONITORING_ALERT_THRESHOLD_POLICY_V0.1.md` |
| Monitoring / Alert Threshold Evaluation | `MONITORING_ALERT_THRESHOLD_EVALUATION_CONTRACT_V0.1.md` |
| Local Reversible Sandbox Candidate Test Plan | `LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_PLAN_V0.1.md` |
| Local Reversible Sandbox Candidate Test Review | `LOCAL_REVERSIBLE_SANDBOX_CANDIDATE_TEST_REVIEW_CONTRACT_V0.1.md` |
| I0.4.7 Review-Only Boundary | `CONTROL_SUPERVISION_THRESHOLD_SANDBOX_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| I0.4 Consolidated Contract Set Index | `I0_4_RUNTIME_CONTRACT_SET_INDEX_V0.1.md` |
| I0.4 Consolidation Checkpoint | `I0_4_CONSOLIDATION_CHECKPOINT_V0.1.md` |
| I0.4 Consolidation Boundary | `I0_4_CONSOLIDATION_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| I0.5 Read-only Runtime Input Bundle | `READ_ONLY_RUNTIME_INPUT_BUNDLE_CONTRACT_V0.1.md` |
| I0.5 Read-only Runtime Kernel | `READ_ONLY_RUNTIME_KERNEL_CONTRACT_V0.1.md` |
| I0.5 Deterministic Contract Inspection Worker | `DETERMINISTIC_CONTRACT_INSPECTION_WORKER_CONTRACT_V0.1.md` |
| I0.5 Read-only Runtime Run | `READ_ONLY_RUNTIME_RUN_CONTRACT_V0.1.md` |
| I0.5 Read-only Runtime Boundary | `I0_5_READ_ONLY_RUNTIME_BOUNDARY_V0.1.md` |

## 3. Superseded Runtime Shorthand

The following older shorthand is not Runtime source of truth.

- Comparing `ALLOW`, `APPROVAL_REQUIRED`, or `BLOCK` with P0–P6.
- Treating Permission Decision as an authority level.
- Flat Task fields that do not separate Authority, Permission, Routing, Validation, Lifecycle, and Audit.
- Agent Output v0.1 fields that do not preserve Assignment ID, Role ID, and Role Version.
- Mixed budget field names that do not follow `execution_budget.v0.1`.
- `none | low | medium | high | critical` as canonical Task risk values.
- `allow | allow_with_report | require_approval | deny | escalate` as canonical Permission Decision values.
- Any legacy `Permission Levels` heading that labels P0-P6. P0-P6 are Authority Levels.
- Any legacy `default_permission: Pn` field. A P-level is an Authority level or ceiling, not a Permission Decision.
- Risk-to-decision tables are default Policy dispositions only. Risk does not independently prove Authority sufficiency or grant Permission.
- Any legacy statement that allows `Task v0.2 or later` for a new Role Assignment. New Runtime Role Assignments require `task.v0.3`, `core_context_binding.v0.3`, and `role_assignment.v0.2`.

Canonical Task risk:

```text
GREEN
YELLOW
ORANGE
RED
```

Canonical Permission Decision:

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

Canonical Task Permission evaluation status:

```text
NOT_EVALUATED
DECIDED
SUPERSEDED
```

## 4. Runtime Precedence

```text
Current Approved Thomas Core Release
↓
Core Context Binding v0.3
↓
Active Operating Constitution
(skip while inactive)
↓
MVP Operating Policy
↓
This Runtime Contract Addendum
↓
Canonical Runtime Contracts
↓
Thomas Prime Charter
↓
MVP Dynamic Role Contract
↓
Role Definition
↓
Role Assignment
↓
Runtime Defaults
```

Organization Architecture defines system structure and long-term boundaries. It does not independently grant Runtime permission.

## 5. New Runtime Record Versions

After adoption of this addendum, new records use:

```text
core_context_binding.v0.3
task.v0.3
permission_decision.v0.3
approval.v0.1
action_fingerprint_payload.v0.1
thomas_permission_approval_operating_policy.v0.1
tool_request.v0.1
program_request.v0.1
resource_request_fingerprint_payload.v0.1
execution_request.v0.1
execution_result.v0.1
validation_result.v0.1
audit_event.v0.1
execution_request_fingerprint_payload.v0.1
audit_event_fingerprint_payload.v0.1
role_assignment.v0.2
agent_output.v0.2
execution_budget.v0.1
```

Existing historical records remain valid under their original schema versions.

New Runtime Role Assignments accept only `task.v0.3`. Historical Task v0.2 records are immutable historical records and must not be used to create new Runtime Assignments.

Role Definition schema version is independent from Role version. Existing `role_definition.v0.2` definitions may remain valid when Registry hashes and canonical contract checks pass. New or materially modified Role Definitions use the canonical `role_definition.v0.3` template.

## 6. Migration Rule

Do not silently rewrite historical records.

Migration creates a new versioned record or an explicit compatibility view.

Material Task changes supersede affected Permission Decisions, invalidate affected approvals and Role Assignments, and preserve the prior records for Audit.


## 7. Core Rule Resolution

Rule ID alone is not sufficient for exact historical interpretation.

```text
Core Release ID
+
Active Core SHA256
+
Rule ID
→ Exact Rule meaning
```

Task, Role Assignment, and Agent Output use the same `core_context_binding_id`.

Core approval and Binding do not grant execution Permission.


## 8. Release and Activation Source of Truth

```text
Self-Contained Core Release Snapshot
↓
Runtime-Authoritative Approval
↓
Committed Activation Record
↓
CURRENT_CORE_RELEASE.yaml
↓
Core Context Binding v0.3
```

The current working-tree Core is a development source.

The bound Release snapshot is the historical Runtime source of truth.

Review evidence is not an Approval record. `REVIEW_CORE_RELEASE.yaml`, PR review, and operator review do not constitute Runtime-Authoritative Approval and cannot activate Runtime use.

Core activation does not grant execution Permission.

## 9. Review-Only Permission and Approval Foundation

```text
Task v0.3
↓
Core Context Binding v0.3
↓
Authority chain
↓
Permission Decision v0.3
↓
Action Approval v0.1 when required
↓
Review evidence only
```

The v0.3/v0.1 foundation does not authorize executor handoff.

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  executor_handoff_allowed: false
  external_execution_allowed: false
  financial_execution_allowed: false
  runtime_mutation_allowed: false
  tool_enablement_allowed: false
  program_enablement_allowed: false
  permission_expansion_allowed: false
```

An Action Approval is separate from Runtime-Authoritative Core Approval.

Approval cannot expand Authority, activate a Tool or Program, or create external execution permission.

Real Approval consumption and Restricted Execution Service integration require a later separately approved Runtime contract.

Every Permission Decision and Action Approval must bind the exact Thomas-approved operating policy:

```yaml
policy_id: thomas.permission_approval.operating_policy
policy_version: 0.1.0
policy_ref: docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml
```

The operating model is `BOUNDED_MAXIMUM_AUTONOMY`: safe internal work is autonomous, reversible changes are executed and reported, material external or protected changes require Approval, and prohibited or uncertain actions fail closed.

## 10. Review-Only Tool and Program Requests

```text
Task and Core Binding
↓
Role Definition and Assignment allowlists
↓
Registry lookup
↓
Authority and Permission
↓
Tool Request v0.1 or Program Request v0.1
↓
Review result only
```

Request creation is allowed by the Thomas Operating Policy. Resource execution is separate and remains unavailable in I0.4.3.

A valid Request cannot activate a Tool or Program, mutate a Registry, hand work to an executor, or expand Authority.

Current Candidate and Disabled Registry entries must produce explicit blocked evidence.

## 11. Review-Only Execution, Validation, and Audit Foundation

```text
Tool Request / Program Request / Action Permission
↓
Execution Request v0.1
↓
Execution Result v0.1
↓
Validation Result v0.1
↓
Audit Event v0.1
```

I0.4.4 creates contracts and evidence only. It does not create an Executor Registry, Restricted Execution Service, executor handoff, Tool execution, Program execution, external action, financial action, Runtime mutation, Approval consumption, or Permission expansion.

Validation can return `PASS`, `REVISE`, or `BLOCK`, but never grants Permission.

Audit is append-only evidence and never authorizes the recorded action.

Current Execution Requests remain blocked because no Executor is registered, enabled, or implemented.

## 12. I0.4.5 Disabled Executor Foundation

I0.4.5 adds an empty non-Runtime Executor Registry design, readiness review, disabled Restricted Execution Service evidence, Hot-Path revalidation preview, Approval consumption preview, and rollback/recovery plan.

No Executor is registered, enabled, implemented, called, or handed an Execution Request. No Approval is consumed and no execution token is issued.

## 13. I0.4.6 Operations Evidence and Executor Candidate Intake

I0.4.6 adds offline evidence records for Monitoring, Alert, Health, Clock, and Kill Switch review plus Executor Candidate Intake and Review records.

No daemon, network probe, notification delivery, clock mutation, process control, Kill Switch command dispatch, Executor registration, Registry mutation, activation, handoff, Approval consumption, or execution token is created.

## 14. I0.4.7 Control, Supervision, Threshold, and Sandbox Foundation

I0.4.7 adds metadata-only private Control Channel identity-binding design, non-dispatched command envelopes, disabled process-supervisor and scheduler interfaces, Review-draft Monitoring/Alert thresholds with offline evaluation, and a not-run local reversible Sandbox candidate test plan and review.

No provider connection, Runtime identity verification, challenge, command dispatch, process observation/control, scheduler installation/enablement/dispatch, Task creation, threshold policy activation, alert delivery, remediation, Kill Switch trigger, Sandbox creation/test execution, filesystem write, network call, subprocess, secret access, Executor registration, activation, handoff, or Runtime effect is created.

## 15. I0.4 Consolidation Checkpoint

The I0.4.2-I0.4.7 functional contract set is indexed and frozen for I0.5 Read-only Runtime Kernel design. The index classifies canonical record contracts, policy helpers, and phase-boundary evidence without creating a Runtime registry or activation.

I0.4 receives no new functional contract families after this checkpoint except defect correction, security hardening, compatibility repair, missing-validator coverage, or an explicit Thomas-approved governance correction.

The checkpoint grants no Core Approval/Activation, Runtime permission, Tool/Program/Executor enablement, Approval consumption, Control Channel dispatch, process/scheduler control, Sandbox execution, external execution, financial execution, Permission expansion, or Authority expansion.

## 16. I0.5 Read-only Runtime Kernel Candidate

I0.5 introduces a deterministic, non-authoritative `DEVELOPMENT_REPLAY` kernel that reads an exact hash-bound Bundle, validates Task/Core/Role/Assignment/Registry lineage, evaluates Authority and Permission, invokes one built-in no-model/no-Tool/no-Program Worker, and returns Agent Output, Validation, Audit, and a final Task snapshot entirely in memory.

The I0.5 component registry is review-only and is not a Runtime source of truth. `RUNTIME_READ_ONLY`, external execution, filesystem mutation, model invocation, Tool/Program execution, Approval consumption, Executor handoff, Scheduler dispatch, Control Channel dispatch, Permission expansion, Authority expansion, and Core activation remain disabled.

The development replay result is executable integration evidence only. It does not replace the separate Repository Gate, immutable Release, Runtime-authoritative Core lifecycle, or future Runtime enablement review.

## I0.5.1 Promotion Readiness Addendum

`RUNTIME_COMPONENT_ATTESTATION_CONTRACT_V0.1.md` and `RUNTIME_PROMOTION_READINESS_CONTRACT_V0.1.md` are review-only evidence contracts. They may block promotion readiness but cannot grant Permission, Authority, Core activation, Runtime activation, Tool/Program enablement, or execution capability.

## I0.5.1 Rev2 Verified Evidence Addendum

`GITHUB_CI_EVIDENCE_CONTRACT_V0.1.md` and `I0_5_1_REV2_VERIFIED_EVIDENCE_BOUNDARY_V0.1.md` provide review-only evidence constraints. They can only block or support a future Thomas design decision. They cannot create Current Core, grant Permission/Authority, activate Runtime, or enable execution capability.

## I0.5.2 Entry Design Addendum

The I0.5.2 Entry Plan and Disabled Entry Adapter may only block or prepare an exact future Thomas review. `READY_FOR_THOMAS_ENTRY_APPROVAL_DESIGN` is not Runtime permission, Runtime activation, entry authorization, Approval consumption, or Executor handoff.
