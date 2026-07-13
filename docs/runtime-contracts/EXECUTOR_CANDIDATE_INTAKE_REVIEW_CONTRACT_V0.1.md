# Executor Candidate Intake Review Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

The review may place a proposal in a review backlog, but it cannot create a Registry candidate, activate an Executor, grant Permission, consume Approval, or hand off an Execution Request. Any missing or failed prerequisite keeps `ready_for_registry_candidate_record`, `ready_for_activation_review`, and `ready_for_executor_handoff` false.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `executor_candidate_intake_review.v0.1`. |
| `review_id` | Immutable intake-review identifier. |
| `intake_ref` | Reviewed Intake reference. |
| `candidate_ref` | Reviewed candidate reference. |
| `checks` | Explicit prerequisite checks and evidence. |
| `summary` | NOT_READY summary, count parity, and missing prerequisites. |
| `decision` | Review-backlog-only decision with no Registry or activation mutation. |
| `runtime_effect` | All operational, registration, activation, and handoff flags false. |
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

## I0.4.7 Sandbox Test Plan Boundary

A local reversible Sandbox test plan and NOT_RUN review may support the review backlog only. They cannot authorize a test run, register a candidate, activate an Executor, or permit handoff.
