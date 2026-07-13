# Executor Candidate Intake Contract v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.6`

## 1. Purpose

An accepted intake means only that a candidate proposal entered the review backlog. It does not create an Executor Registry entry. Secret values, private keys, API secrets, passphrases, and hidden credentials are forbidden. Only metadata references and fingerprints may appear.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `executor_candidate_intake.v0.1`. |
| `intake_id` | Immutable intake identifier. |
| `candidate` | Proposed Executor identity, class, version, implementation reference, and hash. |
| `scope` | Proposed action, target, data, and risk-bearing capabilities. |
| `authority` | Required and maximum authority declarations; no expansion allowed. |
| `security` | Metadata-only secret boundary and privileged capability declarations. |
| `runtime_prerequisites` | Monitoring, alerting, health, clock, Kill Switch, Hot-Path, Approval, rollback, idempotency, and validation requirements. |
| `evidence` | Test, fixture, hash, no-secret, rollback, and monitoring evidence references. |
| `intake_decision` | Review intake result; Runtime Registry, activation, and handoff eligibility remain false. |
| `intake_fingerprint_payload` | Canonical intake fingerprint payload. |
| `intake_fingerprint` | Deterministic SHA-256. |
| `runtime_effect` | No Registry mutation, registration, activation, handoff, or side effect. |
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
