
# Resource Request Review-Only Boundary v0.1

**Document Version:** `0.1.0`
**Status:** `Active Review-Only Boundary`
**Owner:** `Thomas`

## 1. Purpose

This document defines the shared safety boundary for `tool_request.v0.1` and `program_request.v0.1`.

A Resource Request is an auditable request packet. It is not a Runtime executor, capability grant, resource activation, or permission grant. A Resource Request does not execute a Tool or Program.

## 2. Canonical Flow

```text
Task v0.3
↓
Core Context Binding v0.3
↓
Role Definition and Role Assignment scope
↓
Tool or Program Registry lookup
↓
Authority calculation
↓
Permission Decision v0.3
↓
Tool Request v0.1 or Program Request v0.1
↓
Review result only
```

## 3. Shared Invariants

- Exact Resource ID and Version are required.
- Registry status, `enabled`, and `runtime_implementation_available` are checked independently.
- Role Definition allowlist and Role Assignment allowlist are both required.
- A Tool or Program cannot expand Authority.
- Numeric Task and Assignment budget remains binding.
- Permission Decision must bind the exact action fingerprint.
- Approval, when required, cannot activate a Resource or create executor authority.
- Unregistered, disabled, deprecated, archived, version-mismatched, or implementation-unavailable Resources fail closed.
- A Request record may document a block result without making the Resource executable.
- Request creation is allowed by Policy; Resource execution is a separate decision.

## 4. Current Runtime Effect

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  request_record_can_execute: false
  executor_handoff_allowed: false
  tool_execution_allowed: false
  program_execution_allowed: false
  resource_enablement_allowed: false
  registry_mutation_allowed: false
  runtime_mutation_allowed: false
  external_execution_allowed: false
  financial_execution_allowed: false
  permission_expansion_allowed: false
```

## 5. Registry State Rule

Current Registry entries remain Candidate and Disabled.

A valid blocked Request packet is useful evidence because it proves that the system can identify the Resource and explain why execution is unavailable.

```text
Request record valid
≠
Resource Runtime eligible
```

## 6. Future Boundary

Real Tool or Program execution requires later, separately approved Runtime work including:

- active and enabled Registry records,
- implemented adapters or deterministic Runtime,
- hot-path Authority and Permission re-check,
- execution request and result contracts,
- timeout, retry, idempotency, and cancellation behavior,
- Audit Event integration,
- Kill Switch integration,
- monitoring and rollback evidence.

No later phase may infer execution permission from the existence of this contract.

## Downstream I0.4.4 Boundary

A Resource Request may become an upstream input to Execution Request v0.1. This creates review evidence only. It does not change Resource eligibility, activate an Executor, or permit execution.
