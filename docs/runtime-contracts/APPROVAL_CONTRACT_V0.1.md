# Action Approval Contract v0.1

**Schema Version:** `approval.v0.1`
**Document Version:** `0.1.1`
**Status:** `Thomas-Approved Policy-Bound Review-Only Foundation`
**Owner:** `Thomas`

## 1. Purpose

Action Approval records Thomas's exact decision about one action-bound Permission Decision.

This is different from Runtime-Authoritative Core Approval.

```text
Core Release Approval
→ approves one exact Core Release for activation

Action Approval
→ reviews one exact requested action
```

Neither approval type grants the other.

This v0.1 contract remains `REVIEW_ONLY` and cannot hand work to an executor.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact schema identifier |
| `approval_id` | Immutable Action Approval ID |
| `permission_decision_id` | Bound Permission Decision |
| `permission_decision_ref` | Reviewable source record |
| `trace_id` | End-to-end trace lineage |
| `task_id` | Bound Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Context Binding |
| `operating_policy` | Exact Thomas-approved operating policy ID, version, and reference |
| `action_fingerprint` | Exact action hash |
| `approved_action_snapshot` | Canonical payload snapshot |
| `approval_scope` | Must remain `REVIEW_ONLY` |
| `status` | Approval lifecycle status |
| `approver` | Required Thomas identity and verification evidence |
| `decision` | Reason and decision time |
| `consumption` | One-time-use policy and preview-only evidence |
| `validity` | Issue and expiration times |
| `runtime_effect` | Review-only hard guards |
| `audit_refs` | Required audit lineage |

## 3. Thomas-Approved Operating Policy Binding

Every Action Approval must bind the same operating policy as its referenced Permission Decision:

```yaml
operating_policy:
  policy_id: thomas.permission_approval.operating_policy
  policy_version: 0.1.0
  policy_ref: docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml
```

The policy is defined by:

- `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md`;
- `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml`.

Policy mismatch is fail-closed.

Approval cannot lower a policy disposition, expand Authority, bypass a Kill Switch, activate a Tool or Program, or authorize an executor.

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

`CONSUMPTION_PREVIEWED` is not real Approval consumption.

It proves that one-time-use checks can be evaluated without creating an execution token or external effect.

## 5. Exact Binding

The following must match the referenced Permission Decision:

```text
permission_decision_id
trace_id
task_id
task_revision
core_context_binding_id
action_fingerprint
approved_action_snapshot
```

A changed target, content hash, amount, Tool, Program, scope, Task revision, Core Context Binding, or expiration requires a new Permission Decision and a new Approval.

## 6. Thomas Verification

`APPROVED`, `REJECTED`, `REVOKED`, and `CONSUMPTION_PREVIEWED` require:

```yaml
approver:
  required_approver: Thomas
  approved_by: Thomas
  verification_status: VERIFIED
  identity_verification_method: telegram_private_control_channel
  verification_ref: telegram:private_chat:<registered-reference>
```

The approved MVP Control Channel is an authenticated Thomas Telegram private 1:1 chat.

Group messages, channel messages, other users, forwarded messages, emoji-only reactions, ambiguous expressions, stale messages without the matching Approval code, and approvals for a different action are invalid.

This contract validates evidence fields. It does not implement Telegram authentication. The future Control Channel Runtime must verify the registered Thomas User ID, registered private Chat ID, exact Approval ID or action-fingerprint code, explicit decision expression, and expiration.

No script in this package automatically changes `PENDING` to `APPROVED`.

## 7. One-Time-Use Policy

```yaml
consumption:
  one_time_use: true
```

In v0.1:

```text
NOT_CONSUMED
PREVIEWED_ONLY
```

Real Runtime consumption is intentionally absent. It belongs to the future Restricted Execution Service stage and must use a separately approved Runtime contract.

## 8. Expiration

An Approval decision after `validity.expires_at` is invalid.

The Thomas-approved default Approval TTL is 30 minutes.

Scope-specific maximum TTL:

```yaml
EXTERNAL_COMMUNICATION: 5
PUBLICATION: 5
FINANCIAL_NEW_COMMITMENT: 5
DESTRUCTIVE_CHANGE: 5
SECURITY_SENSITIVE_CHANGE: 5
PROTECTED_BRANCH_CHANGE: 15
RELEASE_DEPLOYMENT: 15
RUNTIME_GOVERNANCE: 15
ROLE_GOVERNANCE: 15
TOOL_PROGRAM_GOVERNANCE: 15
CANDIDATE_ROLE_TRIAL: 30
SENSITIVE_MEMORY_GOVERNANCE: 30
```

The machine-readable operating policy is the source of truth.

Expired, revoked, rejected, or superseded Approval cannot be used by a future executor.

The current package cannot use any Approval for execution, including `APPROVED`.

## 9. Review-Only Runtime Guards

Every record must contain:

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

## 10. Fail-Closed Conditions

Block validation when:

- Permission Decision is not `APPROVAL_REQUIRED`;
- lineage differs from the Permission Decision;
- action fingerprint differs;
- action snapshot differs;
- Thomas verification evidence is missing for a decided state;
- decision time is after expiration;
- one-time-use preview evidence is incomplete;
- any Runtime guard becomes true;
- Approval attempts to expand Authority.

## 11. Non-Goals

This contract does not implement:

- automatic Approval;
- identity verification transport;
- executor handoff;
- real Approval consumption;
- external or financial action;
- Runtime configuration mutation;
- Core Release activation.

## I0.4.5 Consumption Boundary

`approval_consumption_preview.v0.1` may verify static eligibility but cannot change Approval status, write a consumption marker, perform compare-and-set, or issue an execution token.
