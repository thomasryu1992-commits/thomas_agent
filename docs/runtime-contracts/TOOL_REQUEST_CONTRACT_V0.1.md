
# Tool Request Contract v0.1

**Schema Version:** `tool_request.v0.1`
**Document Version:** `0.1.0`
**Status:** `Active Review-Only Contract`
**Owner:** `Thomas`

## 1. Purpose

A Tool Request records one exact request to use one exact Tool Version for one exact Task revision and Core Context Binding.

It preserves Registry evidence, Role and Assignment allowlists, Authority, Permission, budget, exact-action fingerprint, validation, and fail-closed review status.

A Tool Request does not execute a Tool. The current mode is `REVIEW_ONLY`. A Tool cannot expand Authority.

## 2. Required Fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact contract version |
| `tool_request_id` | Unique Tool Request ID |
| `trace_id` | End-to-end trace |
| `task_id` | Bound Task |
| `task_revision` | Exact Task revision |
| `core_context_binding_id` | Exact Core Binding |
| `operating_policy` | Thomas-approved policy binding |
| `requested_by` | Actor lineage |
| `role_scope` | Role Definition and Assignment allowlist evidence |
| `resource` | Exact Registry Tool snapshot |
| `operation` | Exact requested operation, target, data, and inputs |
| `authority` | Required, ceiling, granted, effective, and sufficient values |
| `permission` | Exact Permission Decision and action fingerprint binding |
| `budget` | Numeric request and Assignment budget evidence |
| `validation` | Registry, scope, budget, Permission, and lineage checks |
| `request_fingerprint_payload` | Canonical Resource Request fingerprint payload |
| `request_fingerprint` | Deterministic SHA-256 of the canonical payload |
| `runtime_effect` | Review-only non-execution guards |
| `lifecycle` | Review state, timestamps, and supersession |
| `audit_refs` | Related Audit references |

## 3. Resource Eligibility

Runtime eligibility requires all of the following:

```text
Registry ID match
+
Exact Version match
+
status: active
+
enabled: true
+
runtime_implementation_available: true
+
Role Definition allowlist
+
Role Assignment allowlist
+
Authority sufficient
+
Budget available
+
Permission Decision bound to exact action
```

Failure of any required condition results in `BLOCK`.

## 4. Tool-Specific Scope

Every Tool Request must define:

- `tool_id`
- `tool_version`
- `tool_class`
- `operation_type`
- `permission_scope`
- `target_ref`
- `data_scope`
- `input_refs`
- `normalized_parameters`
- `expected_result_contract`

Tool input must contain references or hashes, not Secret values.

## 5. Authority and Permission

The Tool cannot raise the requesting Actor's Authority.

```text
required_permission_level
<=
effective_permission_level
<=
assignment_granted_permission_level
<=
role_permission_ceiling
```

The Resource-required level is also checked. The effective requirement is the stricter of the request and Registry requirements.

Permission Decision is evaluated separately and must reference the same Task, Task revision, Core Binding, Tool ID, target, scope, and action fingerprint.

## 6. Budget

The Request must show:

- Assignment budget reference,
- requested Tool call count,
- requested Runtime seconds,
- requested cost when applicable,
- remaining Tool call count,
- `within_assignment_budget`.

Zero remaining Tool calls results in `BLOCK`.

## 7. Review Result

```text
REVIEW_READY
BLOCK
```

`REVIEW_READY` means the packet is internally consistent. It does not mean the Tool may execute in the current phase.

Current I0.4.3 Runtime effect remains Review-only.

## 8. Fail-Closed Conditions

- Unknown Tool.
- Version mismatch.
- Candidate, disabled, deprecated, or archived Tool.
- `enabled: false`.
- Runtime implementation unavailable.
- Missing Role Definition or Assignment allowlist.
- Authority insufficient.
- Permission Decision mismatch.
- Action fingerprint mismatch.
- Task revision or Core Binding mismatch.
- Target or Data Scope missing.
- Budget exhausted or exceeded.
- Secret-bearing input.
- Kill Switch or Policy conflict.

## 9. Final Rule

> A Tool Request can prove that a Tool request was evaluated. It cannot make the Tool executable.

## Downstream Execution Boundary

A valid Resource Request may be referenced by `execution_request.v0.1`, but the Resource Request itself does not execute and does not authorize Executor handoff.

I0.4.4 remains Review-only. Current Candidate or disabled Resources remain blocked, and no Executor exists.
