# Disabled Restricted Execution Service Interface v0.1

**Evidence Schema:** `disabled_executor_evidence.v0.1`
**Status:** `DISABLED_EVIDENCE_ONLY`

## 1. Purpose

This interface proves the fail-closed behavior expected from a future Restricted Execution Service while deliberately omitting all execution adapters and external clients.

## 2. Required Fields

| Field | Requirement |
| --- | --- |
| `schema_version` | Exact `disabled_executor_evidence.v0.1` |
| `evidence_id` | Stable evidence identifier |
| `execution_request_id` | Exact source Execution Request |
| `execution_request_ref` | Source record path |
| `execution_request_fingerprint` | Exact request fingerprint |
| `service` | Disabled service identity and implementation state |
| `decision` | Exact `BLOCKED_DISABLED_SERVICE` result |
| `effects` | All execution and side-effect evidence false |
| `runtime_effect` | All grants and handoffs false |
| `created_at` | UTC timestamp |
| `audit_refs` | Evidence references |

## 3. Disabled Interface Behavior

The implementation accepts a Review-only Execution Request and returns blocked evidence. It must not import or instantiate exchange, HTTP, email, Telegram, browser-write, payment, deployment, shell-execution, or operating-system mutation clients.

## 4. Forbidden Behavior

- network calls
- Tool or Program calls
- subprocess execution
- secret reads
- Approval consumption
- Registry mutation
- Runtime mutation
- external, financial, deployment, or destructive side effects

## 5. Final Rule

> The disabled service demonstrates refusal behavior only. It is not a dormant live Executor and has no hidden enable switch.
