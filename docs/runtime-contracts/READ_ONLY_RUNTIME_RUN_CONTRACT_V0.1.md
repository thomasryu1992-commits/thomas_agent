# Read-only Runtime Run Contract v0.1

**Schema Version:** `read_only_runtime_run.v0.1`
**Document Version:** `0.1.0`
**Status:** `I0.5 Candidate — Development Replay Evidence`
**Owner:** `Thomas`

## 1. Purpose

The Run record captures one complete I0.5 development replay result, including input lineage, preflight checks, Authority, Permission, Routing, Worker counters, in-memory outputs, lifecycle transitions, no-effect evidence, and integrity.

## 2. Required Fields

| Field | Purpose |
| --- | --- |
| `schema_version` | Exact Run schema identifier |
| `run_id` | Immutable Run identifier |
| `run_mode` | Must remain `DEVELOPMENT_REPLAY` |
| `input_bundle` | Bundle reference, fingerprint, and exact input hashes |
| `lineage` | Trace, Task, Binding, Assignment, and Role lineage |
| `kernel` | Exact Kernel and Worker identity |
| `preflight` | PASS/BLOCK checks and reason codes |
| `authority` | Required, effective, granted, and ceiling Authority evidence |
| `permission` | Exact Permission Decision evidence |
| `routing` | Exact single-Role route and empty resource requests |
| `worker` | Invocation result and zero-capability counters |
| `outputs` | Agent Output, automatic Contract Validation Result, unchanged source Task snapshot, and Audit Events |
| `lifecycle` | Replay-only state transition evidence; never a claim that the source Task advanced |
| `governance` | Approval/activation reference presence and explicit verification status |
| `effects` | Explicit no-effect evidence |
| `summary` | Completed or blocked result |
| `integrity` | Run fingerprint payload and SHA-256 |
| `created_at` | Run timestamp |

## 3. Result Modes

Positive:

```text
COMPLETED_READ_ONLY_REPLAY
```

Fail-closed:

```text
BLOCKED
```

A blocked run must not invoke the Worker and must contain no Agent Output, Validation Result, final Task, or Audit Event claims.

`filesystem_read_performed` and `filesystem_read_count` report observed reads. A path rejected before any file content is read reports `false` and `0` rather than claiming a read occurred.

`REFERENCES_PRESENT_NOT_VERIFIED` means Approval and Activation identifiers were supplied but their authoritative records were not included and verified. It must never be interpreted as governance approval.

## 4. No-effect Evidence

The following must remain false:

```text
filesystem_write_performed
model_invocation_performed
tool_execution_performed
program_execution_performed
network_call_performed
external_action_performed
financial_action_performed
runtime_mutation_performed
approval_consumed
executor_handoff_performed
scheduler_dispatch_performed
control_command_dispatched
permission_expanded
authority_expanded
core_activation_created
```

## 5. Integrity

The completed Run fingerprint binds:

- Run ID;
- Bundle ID and SHA-256;
- Task and Core Binding lineage;
- Assignment ID;
- Agent Output SHA-256;
- Validation Result SHA-256;
- final Task SHA-256;
- ordered Audit Event SHA-256 values;
- creation timestamp.

## 6. Final Rule

> A completed Run proves only that the deterministic read-only replay path passed. It is not Runtime activation, execution permission, or external-action authority.
