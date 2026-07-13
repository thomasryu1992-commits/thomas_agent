# Approval Consumption Preview Contract v0.1

**Schema:** `approval_consumption_preview.v0.1`
**Status:** `PREVIEW_ONLY`

## 1. Purpose

This contract defines how a future one-time Action Approval would be checked and atomically consumed. I0.4.5 performs eligibility preview only and does not mutate the Approval record or issue an execution token.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `approval_consumption_preview.v0.1` |
| `preview_id` | Stable preview identifier |
| `approval` | Exact Approval ID, reference, status, and Action fingerprint |
| `execution_request` | Exact Request ID, reference, and fingerprint |
| `checks` | One-time, status, identity, lineage, scope, fingerprint, and TTL checks |
| `decision` | Preview eligibility without consumption |
| `mutation_evidence` | All state-change and token fields false or null |
| `runtime_effect` | No execution, handoff, or permission expansion |
| `created_at` | UTC timestamp |
| `expires_at` | Short preview validity window |
| `audit_refs` | Evidence references |

## 3. Atomic Future Design

A later Runtime implementation must perform compare-and-set consumption against the exact Approval version, Action fingerprint, Task revision, Core Binding, and unconsumed state. The state transition and execution handoff must not be separable by an unguarded race window.

## 4. I0.4.5 Guard

```yaml
consumption_performed: false
approval_status_changed: false
execution_token_issued: false
```

`ELIGIBLE_FOR_FUTURE_ATOMIC_CONSUMPTION` is evidence that the static Approval fields match. It is not a consumed Approval.

## 5. Final Rule

> Previewing consumption must never spend the one-time Approval or create a transferable execution credential.
