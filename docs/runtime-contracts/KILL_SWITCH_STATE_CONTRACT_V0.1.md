# Kill Switch State Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

The I0.4.6 record is `REVIEW_ONLY_UNBOUND`. It defines `/pause`, `/stop <task_id>`, `/kill`, and `/resume` semantics but does not connect Telegram, mutate Runtime state, stop a process, or resume execution. Agents, Roles, Programs, and Tools cannot self-resume.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `kill_switch_state.v0.1`. |
| `kill_switch_state_id` | Immutable review-state identifier. |
| `policy_ref` | Thomas-approved operating-policy reference. |
| `control_channel` | Metadata-only future Thomas control-channel binding. |
| `state` | Review-only unbound state; no Runtime connection. |
| `commands` | Declared future command vocabulary. |
| `enforcement` | Fail-closed policy intent without claiming active enforcement. |
| `runtime_effect` | No command dispatch, state mutation, or process control. |
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
