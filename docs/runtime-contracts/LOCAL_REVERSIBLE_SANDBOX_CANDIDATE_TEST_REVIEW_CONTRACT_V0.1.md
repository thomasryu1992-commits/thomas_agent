# Local Reversible Sandbox Candidate Test Review Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Reviews whether a local reversible sandbox candidate test plan is ready to enter a future isolated test phase. I0.4.7 records NOT_RUN_NOT_READY and cannot create the Sandbox, execute a test, write a file, register an Executor, activate a candidate, or authorize handoff.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `local_reversible_sandbox_candidate_test_review.v0.1`. |
| `review_id` | Immutable review identifier. |
| `test_plan_ref` | Exact plan reference. |
| `test_plan_fingerprint` | Exact plan SHA-256. |
| `checks` | Structured readiness checks. |
| `summary` | Count parity, missing prerequisites, and NOT_RUN_NOT_READY result. |
| `execution_evidence` | All Sandbox and test effects false. |
| `runtime_effect` | All operational flags false. |
| `created_at` | Review time. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

Review backlog acceptance is not test authorization, test execution, Registry admission, activation review, or Executor handoff.
