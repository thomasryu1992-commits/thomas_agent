
# Program Request Contract v0.1

**Schema Version:** `program_request.v0.1`
**Document Version:** `0.1.0`
**Status:** `Active Review-Only Contract`
**Owner:** `Thomas`

## 1. Purpose

A Program Request records one exact request to invoke one exact Program Version with explicit inputs, parameters, determinism expectations, output contract, Authority, Permission, and budget evidence.

A Program Request does not execute a Program. The current mode is `REVIEW_ONLY`.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact contract version |
| `program_request_id` | Unique Program Request ID |
| `trace_id` | End-to-end trace |
| `task_id` | Bound Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Binding |
| `operating_policy` | Thomas-approved policy binding |
| `requested_by` | Actor lineage |
| `role_scope` | Role Definition and Assignment allowlist evidence |
| `resource` | Exact Registry Program snapshot |
| `invocation` | Exact inputs, hashes, parameters, determinism, and expected output |
| `authority` | Required, ceiling, granted, effective, and sufficient values |
| `permission` | Exact Permission Decision and action fingerprint binding |
| `budget` | Numeric request and Assignment budget evidence |
| `validation` | Registry, scope, budget, Permission, determinism, and lineage checks |
| `request_fingerprint_payload` | Canonical Resource Request fingerprint payload |
| `request_fingerprint` | Deterministic SHA-256 of the canonical payload |
| `runtime_effect` | Review-only non-execution guards |
| `lifecycle` | Review state, timestamps, and supersession |
| `audit_refs` | Related Audit references |

## 3. Program Eligibility

Runtime eligibility requires:

```text
Registry ID and Version match
+
status: active
+
enabled: true
+
runtime_implementation_available: true
+
Role Definition and Assignment allowlists
+
Authority sufficient
+
Budget available
+
Permission Decision bound to exact invocation
+
Determinism requirement compatible with Registry
```

## 4. Program Invocation

Every Program Request must define:

- `program_id`
- `program_version`
- `invocation_type`
- `permission_scope`
- `target_ref`
- `input_refs`
- `input_sha256`
- `normalized_parameters`
- `deterministic_required`
- `expected_output_contract`
- `expected_output_ref`

Input references and hashes are both required so the invocation can be reproduced without embedding Secret values.

## 5. Determinism

When `deterministic_required: true`, the Registry entry must declare `deterministic: true`.

A deterministic Program must not silently read unbound state, current time, random sources, or network data outside its exact input contract.

## 6. Authority, Permission, and Budget

A Program cannot expand Authority or bypass Policy.

The Program Request must bind the same exact action fingerprint as its Permission Decision.

Numeric Program-call, Runtime, retry, and cost budgets remain bounded by the Task and Assignment.

## 7. Review Result

```text
REVIEW_READY
BLOCK
```

The current Registry contains no active Programs. Valid current examples therefore demonstrate fail-closed blocked evidence for Candidate Programs.

## 8. Fail-Closed Conditions

- Unknown Program.
- Version mismatch.
- Candidate, disabled, deprecated, or archived Program.
- `enabled: false`.
- Runtime implementation unavailable.
- Missing Role Definition or Assignment allowlist.
- Authority insufficient.
- Permission or fingerprint mismatch.
- Input hash missing or malformed.
- Determinism mismatch.
- Output contract missing.
- Budget exhausted or exceeded.
- Secret-bearing parameter.
- Policy or Kill Switch conflict.

## 9. Final Rule

> A Program Request is invocation evidence and review input. It is not a Program Runtime.

## Downstream Execution Boundary

A valid Resource Request may be referenced by `execution_request.v0.1`, but the Resource Request itself does not execute and does not authorize Executor handoff.

I0.4.4 remains Review-only. Current Candidate or disabled Resources remain blocked, and no Executor exists.
