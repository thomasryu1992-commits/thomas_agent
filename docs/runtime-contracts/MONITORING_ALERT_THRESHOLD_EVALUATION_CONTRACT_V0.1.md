# Monitoring and Alert Threshold Evaluation Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Records an offline evaluation of one metric against one exact candidate threshold policy. It can recommend an Alert Event candidate but cannot create or deliver an external alert, perform remediation, trigger Kill Switch state, or mutate Runtime.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `monitoring_alert_threshold_evaluation.v0.1`. |
| `evaluation_id` | Immutable evaluation identifier. |
| `policy_ref` | Exact candidate policy reference. |
| `policy_fingerprint` | Exact policy SHA-256. |
| `metric` | Metric ID, value, unit, freshness, and data status. |
| `result` | PASS, WARN, CRITICAL, STALE, or NOT_AVAILABLE classification. |
| `decision` | Review-only recommendation without alert delivery. |
| `evaluation_fingerprint_payload` | Canonical evaluation payload. |
| `evaluation_fingerprint` | Deterministic SHA-256. |
| `effects` | No notification, remediation, Kill Switch, or Runtime effect. |
| `runtime_effect` | All operational flags false. |
| `created_at` | Evaluation time. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

A WARN or CRITICAL result is not a delivered alert, not a Kill Switch trigger, not a Permission Decision, and not an execution block unless a future separately approved Runtime policy consumes fresh evidence.
