# Monitoring Snapshot Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

This contract records a bounded monitoring evidence snapshot. It does not start a daemon, poll a live service, probe a network endpoint, or create a continuous monitoring guarantee. Missing, stale, and unavailable sources must be explicit. `runtime_monitoring_ready` and `continuous_monitoring_active` remain false in I0.4.6.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `monitoring_snapshot.v0.1`. |
| `monitoring_snapshot_id` | Immutable snapshot identifier. |
| `scope` | System, environment, and observed component scope. |
| `collection` | Offline evidence collection window and source-health declaration. |
| `metrics` | Explicit metric observations with freshness and evidence. |
| `summary` | Evidence-only summary; never claims continuous Runtime monitoring. |
| `snapshot_fingerprint_payload` | Canonical payload used for SHA-256. |
| `snapshot_fingerprint` | Deterministic SHA-256 of the canonical payload. |
| `runtime_effect` | All operational and side-effect flags remain false. |
| `audit_refs` | Related append-only Audit Event references. |

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
