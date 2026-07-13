# Executor Registry Contract v0.1 — Review-Only Design

**Schema:** `executor_registry.v0.1`
**Status:** `REVIEW_ONLY_DESIGN`
**Owner:** `Thomas`

## 1. Purpose

This contract defines the future Executor Registry shape without registering or enabling a Runtime Executor. The I0.4.5 Registry is design evidence only and is not a Runtime source of truth.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `executor_registry.v0.1` |
| `registry_id` | Stable Registry identifier |
| `document_version` | Semantic version |
| `status` | Exact `REVIEW_ONLY_NO_ACTIVE_EXECUTORS` |
| `owner` | Exact `Thomas` |
| `runtime_source_of_truth` | Must be `false` |
| `governance` | Registration, activation, authority, secret, and Audit invariants |
| `executors` | Empty in I0.4.5 |
| `runtime_effect` | All capability grants remain false |
| `audit_refs` | Review evidence references |

## 3. Review-Only Registry Rule

```yaml
runtime_source_of_truth: false
executors: []
```

The presence of this file does not create a Registry-backed Executor. No Runtime component may interpret this design Registry as evidence that an Executor exists.

## 4. Future Executor Entry Requirements

A later separately approved stage may propose an Executor entry only with an exact ID, version, implementation hash, supported request types, authority ceiling, Tool/Program allowlists, target and data scopes, secret boundary, idempotency support, rollback capability, monitoring, health evidence, Kill Switch integration, and independent validation.

An entry must still be separately activated. Registration never implies enablement.

## 5. Forbidden States in I0.4.5

- non-empty active Executor list
- `runtime_source_of_truth: true`
- registration or activation permission
- enabled or Runtime-ready Executor
- Executor implementation reference
- Tool, Program, external, financial, or Runtime execution authority
- secret value or secret file reference

## 6. Final Rule

> Registry design describes what a future Registry must prove. It does not create an Executor and does not authorize execution.

## I0.4.6 Candidate Intake Boundary

`executor_candidate_intake.v0.1` and its review may populate a review backlog only. They cannot append to, mutate, or activate the Runtime Executor Registry.
