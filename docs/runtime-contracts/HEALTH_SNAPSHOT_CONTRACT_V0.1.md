# Health Snapshot Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

This contract records health evidence without starting, stopping, restarting, healing, or mutating any process. A `HEALTHY_EVIDENCE_ONLY` result does not establish Runtime readiness and does not authorize execution.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `health_snapshot.v0.1`. |
| `health_snapshot_id` | Immutable health evidence identifier. |
| `subject` | Observed system, service, candidate, or control-channel subject. |
| `checks` | Explicit health checks and evidence. |
| `summary` | Evidence-only health summary with count parity. |
| `remediation` | Recommendations only; no automatic remediation. |
| `runtime_effect` | No daemon, restart, process control, or mutation. |
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
