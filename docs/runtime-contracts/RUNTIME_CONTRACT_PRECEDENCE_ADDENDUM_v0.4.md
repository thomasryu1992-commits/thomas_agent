# Runtime Contract Precedence Addendum v0.4

**Status:** `Active MVP Addendum`
**Document Version:** `0.6.0`
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
| Canonical Governance Policy | `../../governance/GOVERNANCE_POLICY.yaml` |
| Authority and Permission explanation | `AUTHORITY_AND_PERMISSION_MODEL.md` (reference only) |
| Permission Decision record | `PERMISSION_DECISION_CONTRACT_V0.3.md` |
| Action Approval record | `APPROVAL_CONTRACT_V0.1.md` |
| Action Fingerprint algorithm | `ACTION_FINGERPRINT_POLICY_V0.1.md` (reference only) |
| Legacy Permission/Approval policy and principles | compatibility / human-readable references only |
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
| Validation Result | `VALIDATION_RESULT_CONTRACT_V0.1.md` |
| Audit Event | `AUDIT_EVENT_CONTRACT_V0.1.md` |
| Deferred architecture requirements | `../../deferred/DEFERRED_ARCHITECTURE.yaml` (non-runtime-authoritative) |
| Executor Foundation Boundary | `EXECUTOR_FOUNDATION_REVIEW_ONLY_BOUNDARY_V0.1.md` |
| Operations Evidence / Intake Boundary | `OPERATIONS_EVIDENCE_EXECUTOR_INTAKE_REVIEW_ONLY_BOUNDARY_V0.1.md` |
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
Canonical Governance Policy
`../../governance/GOVERNANCE_POLICY.yaml`
↓
MVP Operating Policy
(operational guide only)
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

## 5. Active Record Versions and Deferred Record Index

After adoption of this addendum, new records use:

```text
core_context_binding.v0.3
task.v0.3
permission_decision.v0.3
approval.v0.1
action_fingerprint_payload.v0.1
thomas_governance_policy.v1
tool_request.v0.1
program_request.v0.1
resource_request_fingerprint_payload.v0.1
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

Every new Permission Decision and Action Approval must bind the exact canonical Governance Policy. Historical records retain their original binding:

```yaml
policy_id: thomas.governance.policy
policy_version: 1.1.0
policy_ref: governance/GOVERNANCE_POLICY.yaml
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

## 11. Deferred Architecture Boundary

The canonical non-runtime-authoritative owner of future Runtime Entry, Executor, Operations, Control Channel, and Sandbox requirements is:

`../../deferred/DEFERRED_ARCHITECTURE.yaml`

Deferred artifacts are not current Runtime authority. I0.5.1-I0.5.5 are one Deferred Runtime Entry family. Phase-specific contracts, schemas, component indexes, examples, fixtures, and validators remain subordinate evidence until PR #11 cleanup.

The Deferred Gate has one canonical harness:

```bash
python scripts/validate_deferred_architecture.py
python scripts/run_architecture_gate.py --scope deferred --check-only
```

Passing a Deferred check, readiness report, review packet, candidate, or generated artifact does not activate Runtime Entry, consume Approval, write protected state, start a Runtime Session, call the Kernel, register or enable an Executor, start a daemon, dispatch a schedule or Control command, run a Sandbox, or grant external/financial execution.

## 12. Active Validation and Audit

Validation Result and Audit Event remain Active evidence records. Execution Request and Execution Result are Deferred Executor preview records. The shared validator enforces the split through explicit scopes:

```bash
python scripts/validate_execution_validation_audit_contracts.py --scope active
python scripts/validate_execution_validation_audit_contracts.py --scope deferred
```

Validation and Audit never grant Permission, Approval, Authority, activation, or execution.
