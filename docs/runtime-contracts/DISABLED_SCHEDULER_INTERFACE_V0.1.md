# Disabled Scheduler Interface v0.1

**Status:** `Active Review-Only Foundation`
**Owner:** `Thomas`
**Phase:** `I0.4.7`

## 1. Purpose

Defines a future scheduler plan without installing a job, enabling a scheduler, calculating or dispatching a live next run, creating a Task, invoking a Program, or starting a background process. The plan stores a Task template reference rather than a shell command.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `scheduler_plan_review.v0.1`. |
| `plan_id` | Immutable schedule-plan review identifier. |
| `status` | Review-only and not installed. |
| `scheduler` | Disconnected scheduler metadata. |
| `schedule` | Candidate cadence and timezone with no live next-run claim. |
| `task` | Task template reference; no shell command or dispatch. |
| `controls` | Overlap, concurrency, Kill Switch, and activation-review controls. |
| `plan_fingerprint_payload` | Canonical plan payload. |
| `plan_fingerprint` | Deterministic SHA-256. |
| `effects` | No installation, enablement, dispatch, or Task creation. |
| `runtime_effect` | All operational flags false. |
| `created_at` | Review record creation time. |
| `audit_refs` | Related Audit references. |

## 3. Review-Only Boundary

A valid schedule plan does not install or activate a scheduler. Future activation requires separate Thomas approval, Runtime implementation, Kill Switch binding, overlap protection, monitoring, and rollback evidence.
