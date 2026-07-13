# Clock Sync Evidence Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

Clock evidence is observational. I0.4.6 does not contact NTP, mutate the operating-system clock, restart a time service, or assert production-grade clock synchronization. `absolute_offset_ms` must equal `abs(offset_ms)`.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `clock_sync_evidence.v0.1`. |
| `clock_sync_evidence_id` | Immutable clock evidence identifier. |
| `subject` | Observed host or process scope. |
| `observation` | Local/reference timestamps, offset, threshold, and source reference. |
| `assessment` | Evidence-only assessment; never grants Runtime clock readiness. |
| `mutation_evidence` | All clock and time-service mutation flags remain false. |
| `runtime_effect` | No NTP call, system-time change, service restart, or network probe. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

```text
Evidence or Intake Record
≠ Runtime Readiness
≠ Permission
≠ Approval
≠ Activation
≠ Execution
```

All uncertainty, missing evidence, stale evidence, and unavailable Runtime integration fail closed.
