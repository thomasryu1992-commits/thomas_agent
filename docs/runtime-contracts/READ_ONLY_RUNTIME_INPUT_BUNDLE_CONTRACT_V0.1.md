# Read-only Runtime Input Bundle Contract v0.1

**Schema Version:** `read_only_runtime_input_bundle.v0.1`
**Document Version:** `0.1.0`
**Status:** `I0.5 Candidate — Development Replay Only`
**Owner:** `Thomas`

## 1. Purpose

The Input Bundle binds one explicit Task, Core Context Binding, Role Assignment, Role Definition, Role Registry snapshot, Tool Registry snapshot, Program Registry snapshot, and frozen I0.4 Contract Set Index into one hash-addressed read-only development replay input.

The Bundle is not a Runtime activation record, Permission Decision, Approval, Execution Request, or Executor handoff.

## 2. Required Fields

| Field | Purpose |
| --- | --- |
| `schema_version` | Exact Bundle schema identifier |
| `bundle_id` | Immutable Bundle identifier |
| `run_mode` | Must remain `DEVELOPMENT_REPLAY` |
| `refs` | Explicit repository-relative input references |
| `sha256` | Exact SHA-256 for every referenced input |
| `constraints` | Fail-closed read-only and no-effect limits |
| `integrity` | Canonical fingerprint payload and Bundle SHA-256 |
| `created_at` | Bundle creation timestamp |

## 3. Read-only Boundary

Every Bundle must enforce:

```yaml
filesystem_read_only: true
external_network_allowed: false
tool_execution_allowed: false
program_execution_allowed: false
model_invocation_allowed: false
external_action_allowed: false
runtime_mutation_allowed: false
filesystem_write_allowed: false
secrets_allowed: false
```

References must be relative to the declared Repository root, must not escape it, and must not traverse symlinks. Any referenced-file hash mismatch blocks the run before Worker invocation.

The Kernel reads each referenced file once and uses the same in-memory bytes for both SHA-256 verification and parsing. This prevents a file change between verification and parsing from substituting unverified content.

## 4. Integrity

The Bundle fingerprint covers:

```text
Bundle ID
+
Run Mode
+
Exact References
+
Exact Referenced-file SHA-256 values
+
Exact Constraints
+
Created At
```

A changed Task, Binding, Assignment, Role, Registry, I0.4 Index, reference, hash, constraint, or timestamp requires a new Bundle fingerprint.

## 5. Final Rule

> A valid Input Bundle is only an immutable development replay input. It grants no Runtime authority and cannot enable any external or mutating capability.
