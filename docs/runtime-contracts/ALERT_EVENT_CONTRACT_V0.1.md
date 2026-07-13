# Alert Event Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

An Alert Event is evidence that a condition merits attention. It is not proof that Telegram, email, webhook, pager, or any other notification was delivered. I0.4.6 requires `delivery_status: NOT_SENT_REVIEW_ONLY`, zero attempts, and false delivery effects.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `alert_event.v0.1`. |
| `alert_event_id` | Immutable alert evidence identifier. |
| `monitoring_snapshot_ref` | Source monitoring snapshot. |
| `severity` | INFO, WARN, ERROR, or CRITICAL. |
| `alert_type` | Stable alert taxonomy value. |
| `condition` | Observed condition and threshold evidence. |
| `deduplication_key` | Stable key for future deduplication design. |
| `delivery` | Review-only delivery record; no message or webhook is sent. |
| `acknowledgement` | Review acknowledgement state only. |
| `event_fingerprint_payload` | Canonical fingerprint payload. |
| `event_fingerprint` | Deterministic SHA-256. |
| `runtime_effect` | All delivery, process, and side-effect flags remain false. |
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

## I0.4.7 Threshold Policy Boundary

An offline `monitoring_alert_threshold_evaluation.v0.1` may recommend an Alert Event candidate only. The Review-draft threshold policy cannot authorize notification delivery, remediation, or Kill Switch action.
