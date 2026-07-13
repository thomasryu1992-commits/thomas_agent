# Execution Result Contract v0.1

**Schema Version:** `execution_result.v0.1`
**Document Version:** `0.1.0`
**Status:** `THOMAS_APPROVED_REVIEW_ONLY_FOUNDATION`
**Owner:** `Thomas`

## 1. Purpose

Execution Result v0.1 records the outcome of Review-only execution evaluation.

No actual Executor, Tool, Program, external endpoint, financial path, or Runtime mutation is called in this version.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | `execution_result.v0.1` |
| `execution_result_id` | Unique result record |
| `execution_request_id` | Bound request ID |
| `execution_request_ref` | Bound request reference |
| `execution_request_fingerprint` | Exact request fingerprint |
| `trace_id` | Trace lineage |
| `task_id` | Task lineage |
| `task_revision` | Exact revision |
| `core_context_binding_id` | Exact Core binding |
| `result_status` | Review-only result state |
| `execution_evidence` | Proof that no execution occurred |
| `output` | Preview evidence or blockers |
| `metrics` | Zero-call execution metrics |
| `error` | Optional review error |
| `runtime_effect` | Mandatory no-effect guard |
| `lifecycle` | Creation and supersession |
| `audit_refs` | Related Audit Events |

## 3. Result Status

```text
NOT_EXECUTED
BLOCKED
PREVIEWED
EXPIRED
SUPERSEDED
```

`SUCCEEDED`, `FAILED`, `PARTIAL`, fill, send, publish, deploy, payment, and other real execution outcomes are not valid v0.1 statuses.

## 4. No-Execution Evidence

Every record must prove:

```yaml
execution_performed: false
executor_called: false
execution_attempt_id: null
tool_execution_performed: false
program_execution_performed: false
external_side_effect_performed: false
financial_side_effect_performed: false
runtime_mutation_performed: false
```

A `BLOCKED` result requires at least one block reason.

A `PREVIEWED` result may contain only internal preview artifacts and must still contain no execution or side effects.

## 5. Metrics

Current v0.1 metrics remain zero:

```yaml
runtime_seconds: 0
tool_calls: 0
program_calls: 0
external_calls: 0
cost_decimal: '0'
```

## 6. Runtime Effect

Execution Result v0.1 cannot grant Permission, consume Approval, activate an Executor, mutate Runtime, or claim external effects.

## 7. Final Rule

> A Review-only Execution Result records why execution did not occur or what a non-executing preview produced. It must never fabricate a successful execution.

## I0.4.5 Disabled Service Evidence

A disabled Restricted Execution Service may produce `disabled_executor_evidence.v0.1`; this remains separate from a real Execution Result and cannot claim an attempted or completed execution.
