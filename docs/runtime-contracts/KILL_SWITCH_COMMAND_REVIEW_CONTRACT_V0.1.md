# Kill Switch Command Review Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

This record reviews a future control command. It cannot dispatch the command. `RESUME` requires future Runtime verification of Thomas through the approved private control channel, but I0.4.6 performs no such verification or state transition.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `kill_switch_command_review.v0.1`. |
| `command_review_id` | Immutable command-review identifier. |
| `kill_switch_state_ref` | Referenced review-only Kill Switch state. |
| `requested_command` | PAUSE, STOP_TASK, KILL, or RESUME. |
| `requested_by` | Metadata-only requester identity evidence. |
| `target_task_id` | Task target for STOP_TASK, otherwise null. |
| `request_fingerprint_payload` | Canonical command-review payload. |
| `request_fingerprint` | Deterministic SHA-256. |
| `review` | Review-only decision and reasons. |
| `effects` | All dispatch, state, process, scheduler, and resume effects false. |
| `runtime_effect` | All operational flags remain false. |
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

## I0.4.7 Identity Binding Dependency

Future command dispatch requires a fresh Runtime-verified `control_channel_identity_binding.v0.1` successor record and replay-protected command envelope. I0.4.7 Review-only binding and envelope records cannot dispatch or mutate state.
