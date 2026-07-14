# Action Approval Contract v0.1

**Schema Version:** `approval.v0.1`
**Document Version:** `0.2.0`
**Status:** `ACTIVE_RECORD_CONTRACT_POLICY_REFERENCED`
**Owner:** `Thomas`
**Canonical Policy:** [`governance/GOVERNANCE_POLICY.yaml`](../../governance/GOVERNANCE_POLICY.yaml)

## 1. Purpose

`Approval` records Thomas's exact decision about one action-bound `PermissionDecision`.

It is different from Runtime-Authoritative Core Approval:

```text
Core Release Approval
→ approves one exact Core Release for activation

Action Approval
→ records one exact requested-action decision
```

Neither Approval type grants the other. This record contract does not define Approval policy; the canonical Governance Policy owns approver, TTL, one-time-use, action-binding, Control Channel, and conflict rules.

This v0.1 record remains `REVIEW_ONLY` and cannot hand work to an Executor.

## 2. Required Fields

| Field | Meaning |
|---|---|
| `schema_version` | Exact record schema identifier |
| `approval_id` | Immutable Action Approval ID |
| `permission_decision_id` | Bound Permission Decision ID |
| `permission_decision_ref` | Reviewable Permission Decision record |
| `trace_id` | End-to-end trace lineage |
| `task_id` | Bound Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Context Binding |
| `operating_policy` | Exact canonical Governance Policy ID, version, and path |
| `action_fingerprint` | Exact action hash |
| `approved_action_snapshot` | Canonical action payload snapshot |
| `approval_scope` | Must remain `REVIEW_ONLY` |
| `status` | Approval lifecycle state |
| `approver` | Thomas identity and verification evidence |
| `decision` | Decision reason and time |
| `consumption` | One-time-use and preview-only evidence |
| `validity` | Issue and expiration times |
| `runtime_effect` | Review-only hard guards |
| `audit_refs` | Audit lineage |

## 3. Thomas-Approved Operating Policy Binding

Every new Action Approval must bind the same canonical Governance Policy as its referenced Permission Decision:

```yaml
operating_policy:
  policy_id: thomas.governance.policy
  policy_version: 1.1.0
  policy_ref: governance/GOVERNANCE_POLICY.yaml
```

Historical v0.1 records with the previous operating-policy binding remain immutable historical evidence. New records must use the canonical binding.

Policy mismatch is fail-closed. Approval cannot lower a policy disposition, expand Authority, bypass a Kill Switch, activate a Tool or Program, grant Runtime activation, consume itself, or authorize an Executor.

## 4. Status

```text
PENDING
APPROVED
REJECTED
EXPIRED
REVOKED
SUPERSEDED
CONSUMPTION_PREVIEWED
```

`CONSUMPTION_PREVIEWED` is not real Approval consumption. It is evidence that one-time-use checks were evaluated without creating an execution token or external effect.

## 5. Exact binding

The following values must match the referenced Permission Decision:

```text
permission_decision_id
trace_id
task_id
task_revision
core_context_binding_id
operating_policy
action_fingerprint
approved_action_snapshot
```

A changed target, content hash, amount, Tool, Program, scope, Task revision, Core Context Binding, policy binding, or expiration requires a new Permission Decision and a new Approval.

## 6. Thomas verification

Decided states require the approver and identity-verification evidence defined by the canonical Governance Policy. Current records validate evidence fields only; they do not implement Telegram authentication.

No script in this package automatically changes `PENDING` to `APPROVED`.

## 7. One-time-use policy

```yaml
consumption:
  one_time_use: true
```

Current supported evidence states are:

```text
NOT_CONSUMED
PREVIEWED_ONLY
```

Real Runtime consumption is intentionally absent and requires a future separately approved Runtime stage with atomic state protection and hot-path revalidation.

## 8. Expiration

Default and scope-specific TTL values come only from the canonical Governance Policy. An Approval decision after expiration is invalid. Expired, rejected, revoked, superseded, or consumed Approval cannot be used by a future Executor.

The current package cannot use any Approval for execution, including `APPROVED`.

## 9. Review-only Runtime guards

Every record must preserve:

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

## 10. Fail-closed conditions

Validation blocks when the referenced Permission Decision is not `APPROVAL_REQUIRED`, lineage differs, policy binding differs, the action fingerprint or action snapshot differs, Thomas verification evidence is missing for a decided state, a decision is late, one-time-use evidence is invalid, a Runtime guard becomes true, or Approval attempts to expand Authority.

## 11. Non-goals

This contract does not implement automatic Approval, identity-verification transport, Executor handoff, real Approval consumption, external or financial action, Runtime configuration mutation, Permission expansion, Authority expansion, or Core activation.

## I0.4.5 Consumption Boundary

`approval_consumption_preview.v0.1` may verify static eligibility but cannot change Approval status, write a consumption marker, perform compare-and-set, issue an execution token, or hand work to an Executor.
