# Hot-Path Pre-Execution Revalidation Contract v0.1

**Schema:** `pre_execution_revalidation.v0.1`
**Status:** `PREVIEW_ONLY`

## 1. Purpose

This contract defines the checks that must run immediately before any future Executor handoff. I0.4.5 produces a preview record only; it does not create a reusable execution token.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `pre_execution_revalidation.v0.1` |
| `revalidation_id` | Stable record identifier |
| `execution_request` | Exact Request ID, reference, and fingerprint |
| `lineage` | Task revision, Core Binding, Permission, Approval, and Action fingerprint |
| `clock` | Server/client clock evidence and maximum skew |
| `checks` | Complete Hot-Path check set |
| `decision` | Calculated `BLOCKED` or future `READY` result |
| `runtime_effect` | No token, handoff, call, or execution |
| `created_at` | UTC timestamp |
| `expires_at` | Short preview validity window |
| `audit_refs` | Evidence references |

## 3. Mandatory Hot-Path Checks

- Task revision is current
- Core Context Binding is current and not revoked
- Permission Decision is current and exactly bound
- Action and Execution Request fingerprints match
- Approval is exact, verified, unexpired, unrevoked, and unconsumed when required
- Resource is registered, active, enabled, and implemented
- Role Definition and Assignment allowlists still match
- Authority is sufficient
- numeric budget remains available
- idempotency key is unused
- Kill Switch is inactive and Task is not paused or stopped
- target, data scope, input hashes, and normalized parameters are unchanged
- required Validation is PASS and fresh
- rollback/recovery plan is present when required
- Executor is registered, active, enabled, healthy, and compatible
- server/client clock evidence is within tolerance
- no secret-bearing values are present in records

## 4. Freshness

A future Hot-Path decision must be short-lived and non-reusable. I0.4.5 uses a maximum preview window of 30 seconds and still produces no execution token.

## 5. Final Rule

> Earlier approval and validation are necessary evidence, not substitutes for immediate pre-execution revalidation.

## I0.4.6 Runtime Evidence Boundary

Offline Monitoring, Health, Clock, and Kill Switch review records cannot satisfy a future Hot-Path Runtime check. Future execution requires fresh Runtime-bound evidence immediately before handoff.
