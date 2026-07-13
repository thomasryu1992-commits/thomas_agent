# Rollback and Recovery Plan Contract v0.1

**Schema:** `rollback_recovery_plan.v0.1`
**Status:** `REVIEW_ONLY`

## 1. Purpose

This contract describes how a future reversible action would be restored or recovered if execution fails or causes an unacceptable outcome. I0.4.5 creates plans and evidence only.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `rollback_recovery_plan.v0.1` |
| `plan_id` | Stable plan identifier |
| `execution_request` | Exact Request ID, reference, fingerprint, and Action fingerprint |
| `classification` | Rollback requirement, risk class, and reversibility |
| `checkpoint` | Pre-action evidence and hashes |
| `rollback` | Ordered rollback steps and verification criteria |
| `recovery` | Recovery steps, owner, RTO, and data-loss tolerance |
| `validation` | Dry-run and independent review state |
| `plan_fingerprint_payload` | Canonical plan payload |
| `plan_fingerprint` | SHA-256 of the canonical payload |
| `runtime_effect` | No rollback or recovery execution |
| `lifecycle` | Review state and timestamps |
| `audit_refs` | Evidence references |

## 3. Requirements

A rollback-required action may not become future execution-ready unless the plan is complete, checkpoint evidence exists, rollback steps are bounded and ordered, post-rollback verification is explicit, recovery ownership is assigned, and independent review is complete where policy requires it.

## 4. I0.4.5 Guard

```yaml
rollback_performed: false
recovery_performed: false
checkpoint_mutation_performed: false
```

## 5. Final Rule

> A rollback document is not proof that rollback works. Dry validation and later controlled Runtime evidence are separate prerequisites.
