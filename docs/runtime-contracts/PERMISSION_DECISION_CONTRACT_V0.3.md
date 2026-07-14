# Permission Decision Contract v0.3

**Schema Version:** `permission_decision.v0.3`
**Document Version:** `0.4.0`
**Status:** `ACTIVE_RECORD_CONTRACT_POLICY_REFERENCED`
**Owner:** `Thomas`
**Canonical Policy:** [`governance/GOVERNANCE_POLICY.yaml`](../../governance/GOVERNANCE_POLICY.yaml)

## 1. Purpose

`PermissionDecision` is an immutable, action-specific result record. It stores the exact policy result, evidence, lineage, Authority calculation, action fingerprint, Approval requirement, lifecycle, and Review-only guards for one requested action.

It does not define global Authority, Permission, Approval, effect, TTL, Control Channel, or conflict rules. Those rules belong only to the canonical Governance Policy.

Authority does not equal Permission.

```text
Authority lineage
↓
Exact action fingerprint
↓
Canonical Governance Policy evaluation
↓
Permission Decision record
```

A sufficient Authority chain is necessary but not sufficient. The exact action receives one of:

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

This contract remains `REVIEW_ONLY`. It does not create an executor token or grant external execution, financial execution, Runtime mutation, Tool enablement, Program enablement, Approval consumption, or Permission expansion.

## 2. Required Fields

| Field | Meaning |
|---|---|
| `schema_version` | Exact record schema identifier |
| `permission_decision_id` | Immutable Permission Decision ID |
| `trace_id` | End-to-end trace lineage |
| `task_id` | Bound Task ID |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Context Binding |
| `operating_policy` | Exact canonical Governance Policy ID, version, and path |
| `requested_by` | Actor, Role, and Assignment lineage |
| `fingerprint_payload` | Canonical exact-action payload |
| `action_fingerprint` | SHA-256 over the canonical payload |
| `authority` | Required, ceiling, granted, effective, and sufficiency evidence |
| `risk` | Risk evidence and policy-disposition snapshot |
| `decision` | Exact Permission result and constraints |
| `approval` | Approval requirement and bound Approval ID |
| `runtime_effect` | Review-only hard guards |
| `lifecycle` | Active, superseded, or expired state |
| `audit_refs` | Audit lineage |

## 3. Thomas-Approved Operating Policy Binding

Every new Permission Decision must bind the canonical Governance Policy:

```yaml
operating_policy:
  policy_id: thomas.governance.policy
  policy_version: 1.1.0
  policy_ref: governance/GOVERNANCE_POLICY.yaml
```

Historical records bound to `thomas.permission_approval.operating_policy` v0.1.0 remain interpretable under their original schema and history. They are not silently rewritten. New records must use the canonical binding above.

The `operating_policy` field is a reference. This record cannot substitute, copy, or redefine policy rules.

## 4. Authority invariant

The canonical comparison is:

```text
required_permission_level
<= effective_permission_level
<= assignment_granted_permission_level
<= role_permission_ceiling
```

When this chain is false, `permission_decision = BLOCK`.

Approval cannot expand Authority. Approval cannot convert an insufficient Authority chain into an executable action.

## 5. Exact action binding

The action fingerprint binds the exact Task revision, Core Context Binding, requester, Permission scope, action type, target, Tool, Program, data scope, content hash, amount, currency, normalized parameters, and expiration.

Any material change requires a new `action_fingerprint` and a new Permission Decision.

## 6. Risk is not Permission

`risk.policy_disposition` is an evidence snapshot of the canonical policy classification. Risk does not independently prove Authority, grant Permission, activate a Tool or Program, create Approval, authorize an Executor, or authorize an external or financial action.

The final record result is `decision.permission_decision`.

## 7. Approval binding

For `APPROVAL_REQUIRED`:

```yaml
approval:
  approval_required: true
  approval_id: approval_<exact_id>
  approval_status: PENDING
```

For all other decisions:

```yaml
approval:
  approval_required: false
  approval_id: null
  approval_status: NOT_REQUIRED
```

Approval must reference the same Task revision, Core Context Binding, Permission Decision, canonical policy binding, and action fingerprint.

## 8. Lifecycle

```text
ACTIVE
SUPERSEDED
EXPIRED
```

A material Task, action, Core binding, policy binding, target, content, amount, resource, scope, or expiration change creates a new record and supersedes the old record. Historical records are not silently overwritten.

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

An `ALLOW` result is a policy result record, not an Executor token.

## 10. Fail-closed conditions

Validation blocks when Authority lineage is incomplete, Authority is insufficient, Task or Core lineage differs, the canonical Governance binding differs, the action fingerprint is missing or invalid, material action fields changed, Approval binding is invalid, the record is expired or superseded, a Review-only guard is enabled, or a secret-bearing value appears in the fingerprint payload.

## 11. Non-goals

This contract does not implement external execution, financial execution, a Restricted Execution Service, Tool or Program activation, automatic Approval, Telegram identity verification, real Approval consumption, Authority escalation, Runtime activation, or Core activation.
