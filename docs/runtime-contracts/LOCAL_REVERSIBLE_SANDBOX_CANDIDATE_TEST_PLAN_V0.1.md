# Local Reversible Sandbox Candidate Test Plan v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Defines a not-run test plan for the local reversible sandbox Executor candidate. The plan is restricted to a future temporary test root, denies network, secrets, subprocesses, symlink following, path escape, persistent writes, external systems, and Runtime mutation, and requires checkpoint, rollback, hash verification, and cleanup evidence.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `local_reversible_sandbox_candidate_test_plan.v0.1`. |
| `test_plan_id` | Immutable plan identifier. |
| `candidate_intake_ref` | Exact Executor Candidate Intake reference. |
| `candidate_id` | Exact candidate identifier. |
| `status` | Review-only and not run. |
| `environment` | Future local temporary root with all escape and side-effect capabilities denied. |
| `allowed_operations` | Explicit reversible file-operation allowlist. |
| `test_cases` | Positive, rollback, cleanup, escape, secret, network, subprocess, and idempotency cases. |
| `rollback` | Mandatory checkpoint, restore-hash, and cleanup requirements. |
| `activation_boundary` | No Registry mutation, activation, or handoff. |
| `plan_fingerprint_payload` | Canonical plan payload. |
| `plan_fingerprint` | Deterministic SHA-256. |
| `runtime_effect` | All Sandbox and Runtime effects false. |
| `created_at` | Plan creation time. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

This is a test plan, not a Sandbox implementation or test run. No directory, file, process, network connection, secret read, Registry record, Executor activation, or execution handoff is created.
