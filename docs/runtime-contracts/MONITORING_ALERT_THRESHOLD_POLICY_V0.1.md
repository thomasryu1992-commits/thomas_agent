# Monitoring and Alert Threshold Policy v0.1

**Status:** `Review Draft — Not Runtime Active`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Defines conservative candidate thresholds for offline Monitoring and Alert classification. These thresholds are implementation defaults for review, not Thomas-approved Runtime thresholds. They may classify evidence as PASS, WARN, CRITICAL, STALE, or NOT_AVAILABLE but cannot send notifications, trigger a Kill Switch, remediate, restart a process, mutate Runtime, or authorize execution.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `monitoring_alert_threshold_policy.v0.1`. |
| `policy_id` | Stable candidate policy identifier. |
| `policy_version` | Semantic version. |
| `status` | Review draft and not Runtime active. |
| `owner` | Thomas. |
| `evaluation_mode` | Offline Review-only. |
| `rules` | Metric-specific warning, critical, stale, and missing-data rules. |
| `governance` | Explicit no-activation, no-delivery, no-remediation controls. |
| `policy_fingerprint_payload` | Canonical policy payload. |
| `policy_fingerprint` | Deterministic SHA-256. |
| `runtime_effect` | All operational flags false. |
| `created_at` | Draft creation time. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

Threshold classification is evidence, not action. Runtime activation requires a separate exact policy review and approval. Missing, stale, unavailable, and ambiguous data fail closed and cannot be silently treated as healthy.
