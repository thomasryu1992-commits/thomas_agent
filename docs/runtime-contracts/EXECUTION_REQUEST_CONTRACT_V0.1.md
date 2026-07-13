# Execution Request Contract v0.1

**Schema Version:** `execution_request.v0.1`
**Document Version:** `0.1.0`
**Status:** `THOMAS_APPROVED_REVIEW_ONLY_FOUNDATION`
**Owner:** `Thomas`

## 1. Purpose

An Execution Request is an immutable review record that binds one exact upstream Tool Request, Program Request, or action Permission Decision to an intended execution plan.

This v0.1 contract does not execute, call, enable, register, or hand work to an Executor.

```text
Valid Execution Request
≠
Execution permission
≠
Executor readiness
≠
Execution performed
```

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | `execution_request.v0.1` |
| `execution_request_id` | Unique request ID |
| `trace_id` | End-to-end trace |
| `task_id` | Owning Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core binding |
| `operating_policy` | Thomas-approved policy binding |
| `requested_by` | Actor and Assignment lineage |
| `upstream` | Exact Tool, Program, or action Permission input |
| `authority` | Authority chain and sufficiency |
| `permission` | Exact Permission Decision binding |
| `approval` | Action Approval binding when required |
| `execution_plan` | Preview-only intended executor plan |
| `idempotency` | One-action / duplicate-prevention evidence |
| `budget` | Assignment budget snapshot |
| `validation` | Review checks and blockers |
| `request_fingerprint_payload` | Canonical fingerprint payload |
| `request_fingerprint` | SHA-256 of the payload |
| `runtime_effect` | Mandatory Review-only guard |
| `lifecycle` | Review status and expiry |
| `audit_refs` | Related Audit Events |

## 3. Upstream Binding

Exactly one upstream record is bound.

```text
Tool Request v0.1
or
Program Request v0.1
or
Action Permission Decision v0.3
```

The following values must match the referenced upstream record:

- Task ID
- Task revision
- Core Context Binding ID
- request or action fingerprint
- requested operation
- target
- Permission scope
- resource ID and version when applicable

A stale, superseded, expired, or mismatched upstream record blocks the Execution Request.

## 4. Authority and Permission

Authority is evaluated before Permission.

```text
required_permission_level
<= effective_permission_level
<= assignment_granted_permission_level
<= role_permission_ceiling
```

Failure produces `BLOCK`.

Approval cannot repair insufficient Authority.

The bound Permission Decision must match:

- Permission Decision ID
- Permission Decision reference
- action fingerprint
- decision value
- Task revision
- Core Context Binding

## 5. Approval Binding

When the Permission Decision is `APPROVAL_REQUIRED`:

- a verified action-bound Approval reference is required;
- the Approval must bind the same action fingerprint;
- the Approval must be unexpired and not rejected, revoked, or superseded;
- `approval_consumed` remains `false` in this Review-only version.

Approval does not enable an Executor and does not create Runtime authority.

## 6. Execution Plan

The only valid execution mode in v0.1 is:

```yaml
execution_mode: PREVIEW_ONLY
```

Executor fields are intentionally unavailable:

```yaml
executor_id: null
executor_version: null
executor_registry_ref: null
executor_registered: false
executor_enabled: false
executor_implementation_available: false
executor_handoff_allowed: false
```

No Executor Registry is introduced by this phase.

## 7. Fingerprint

`execution_request_fingerprint_payload.v0.1` binds:

- Task ID and revision
- Core Context Binding
- requester
- upstream request type, ID, reference, and fingerprint
- action fingerprint
- Permission Decision ID
- Approval ID when applicable
- executor ID, which is `null` in v0.1
- target and data scope
- normalized parameters
- idempotency key
- Assignment budget reference
- expiration

Any material change requires a new Execution Request and fingerprint.

## 8. Review Result

Current v0.1 results:

```text
REVIEW_READY
BLOCK
```

`REVIEW_READY` means the review packet is structurally complete. It does not mean executable.

Because no Executor is registered, enabled, or implemented in I0.4.4, `executor_ready` is always `false` and current integrated examples remain `BLOCK`.

## 9. Runtime Effect

Every record must contain:

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  request_can_execute: false
  executor_handoff_allowed: false
  executor_call_allowed: false
  tool_execution_allowed: false
  program_execution_allowed: false
  external_execution_allowed: false
  financial_execution_allowed: false
  runtime_mutation_allowed: false
  side_effects_allowed: false
  permission_expansion_allowed: false
```

## 10. Final Rule

> An Execution Request describes one exact intended execution and why it is blocked or review-ready. It does not execute.

## I0.4.5 Downstream Guard

A future Executor handoff requires a fresh `pre_execution_revalidation.v0.1` record, valid atomic Approval consumption, an active Runtime Executor Registry entry, and a validated rollback/recovery plan when policy requires it. I0.4.5 provides previews only.
