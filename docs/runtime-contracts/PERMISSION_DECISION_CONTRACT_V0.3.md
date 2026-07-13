# Permission Decision Contract v0.3

**Schema Version:** `permission_decision.v0.3`
**Document Version:** `0.3.1`
**Status:** `Thomas-Approved Policy-Bound Review-Only Foundation`
**Owner:** `Thomas`

## 1. Purpose

Permission Decision is the immutable, action-specific policy result that answers whether an exact requested action may proceed.

Authority does not equal Permission.

```text
Authority chain
↓
Exact action fingerprint
↓
Risk and policy evaluation
↓
Permission Decision
```

A sufficient Authority level is necessary but not sufficient. The exact action must still receive one of:

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

This v0.3 package is `REVIEW_ONLY`. It does not hand work to an executor and does not grant external execution, financial execution, Runtime mutation, Tool enablement, Program enablement, or Permission expansion.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact schema identifier |
| `permission_decision_id` | Immutable Permission Decision ID |
| `trace_id` | End-to-end trace lineage |
| `task_id` | Bound Task ID |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Context Binding |
| `operating_policy` | Exact Thomas-approved operating policy ID, version, and reference |
| `requested_by` | Actor, Role, and Assignment lineage |
| `fingerprint_payload` | Canonical exact-action payload |
| `action_fingerprint` | SHA-256 of canonical payload |
| `authority` | Required, ceiling, granted, effective, and sufficiency result |
| `risk` | Risk level, reasons, and default policy disposition |
| `decision` | Exact Permission Decision and constraints |
| `approval` | Approval requirement and bound Approval ID |
| `runtime_effect` | Review-only hard guards |
| `lifecycle` | Active, superseded, or expired state |
| `audit_refs` | Required audit lineage |

## 3. Thomas-Approved Operating Policy Binding

Every new Permission Decision must bind the exact machine-readable policy:

```yaml
operating_policy:
  policy_id: thomas.permission_approval.operating_policy
  policy_version: 0.1.0
  policy_ref: docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml
```

The human-readable source is:

`THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md`

The policy uses the `BOUNDED_MAXIMUM_AUTONOMY` model:

```text
Safe and reversible internal work
→ ALLOW

Important but reversible internal work
→ EXECUTE_AND_REPORT

External, financial, production, governance, destructive, or security-sensitive work
→ APPROVAL_REQUIRED

Insufficient Authority, prohibited behavior, or unsafe uncertainty
→ BLOCK
```

A Runtime component cannot substitute another policy ID or version without a versioned policy change, validation, and Thomas approval.

`permission_scope` is part of the action fingerprint and must resolve to a minimum policy disposition in the approved policy. A Permission Decision may be more restrictive than the minimum disposition, but never less restrictive.

## 4. Authority Invariant

The canonical comparison is:

```text
required_permission_level
<= effective_permission_level
<= assignment_granted_permission_level
<= role_permission_ceiling
```

When this chain is false:

```text
permission_decision = BLOCK
```

Approval cannot expand Authority.

An Approval record cannot convert an insufficient Authority chain into an executable action.

## 5. Exact Action Binding

The following values are part of `fingerprint_payload`:

- Task ID
- Task revision
- Core Context Binding ID
- Requester reference
- Permission scope
- Action type
- Target reference
- Tool ID
- Program ID
- Data scope
- Content SHA-256
- Amount and currency
- Normalized parameters
- Expiration

Any material change requires a new `action_fingerprint` and a new Permission Decision.

The following changes invalidate reuse:

```text
Target changed
Content changed
Amount changed
Currency changed
Tool changed
Program changed
Data scope changed
Task revision changed
Core Context Binding changed
Expiration changed
```

## 6. Risk Is Not Permission

`risk.policy_disposition` is a default policy recommendation only.

Risk does not independently:

- prove Authority sufficiency;
- grant Permission;
- activate a Tool;
- activate a Program;
- create Approval;
- authorize an executor;
- authorize external or financial action.

The final result is `decision.permission_decision`.

## 7. Approval Rules

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

Approval must reference the same Task revision, Core Context Binding, Permission Decision, and action fingerprint.

## 8. Lifecycle

```text
ACTIVE
SUPERSEDED
EXPIRED
```

A material Task change, action change, Core rebind, or policy-relevant scope change creates a new record and supersedes the old record.

Historical records are not silently rewritten.

## 9. Review-Only Runtime Guards

Every v0.3 record must contain:

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

An `ALLOW` result in this package is a review result only. It is not an executor token.

## 10. Failure Rules

Fail closed when:

- Authority lineage is incomplete;
- Authority is insufficient;
- Task revision does not match;
- Core Context Binding does not match;
- action fingerprint is missing or incorrect;
- requested target or content changed;
- Approval is required but not correctly bound;
- record is expired or superseded;
- any Review-only Runtime guard is enabled;
- a secret-bearing value appears in the fingerprint payload.

## 11. Non-Goals

This contract does not implement:

- external execution;
- financial execution;
- Restricted Execution Service;
- Tool or Program activation;
- automatic Approval;
- Telegram identity verification;
- Approval consumption for real execution;
- Authority escalation.
