# Read-only Runtime Input Bundle Contract v0.1

**Schema Version:** `read_only_runtime_input_bundle.v0.1`
**Document Version:** `0.2.0`
**Status:** `I0.5 Candidate — Development Replay Only`
**Owner:** `Thomas`

## 1. Purpose

The Input Bundle binds one explicit Task, Core Context Binding, Role Assignment, Role Definition, Role Registry snapshot, Tool Registry snapshot, Program Registry snapshot, active Governance Policy, and frozen I0.4 Contract Set Index into one hash-addressed read-only development replay input.

The Bundle is not a Runtime activation record, Permission Decision, Approval, Execution Request, or Executor handoff.

## 2. Required Fields

| Field | Purpose |
| --- | --- |
| `schema_version` | Exact Bundle schema identifier |
| `bundle_id` | Immutable Bundle identifier |
| `run_mode` | Must remain `DEVELOPMENT_REPLAY` |
| `refs` | Explicit repository-relative input references, including the Governance Policy |
| `sha256` | Exact SHA-256 for every referenced input |
| `governance_binding` | Exact Governance Policy ID, version, repository reference, and file SHA-256 |
| `constraints` | Fail-closed read-only and no-effect limits |
| `integrity` | Canonical fingerprint payload and Bundle SHA-256 |
| `created_at` | Bundle creation timestamp |

## 3. Governance Policy Binding

Every Bundle must bind the exact active Governance Policy through all four fields below:

```yaml
governance_binding:
  policy_id: thomas.governance.policy
  policy_version: <exact policy version>
  policy_ref: governance/GOVERNANCE_POLICY.yaml
  policy_sha256: sha256:<exact file hash>
```

The bound `policy_ref` and `policy_sha256` must exactly match `refs.governance_policy` and `sha256.governance_policy`. The referenced Policy record must match the bound ID and version, remain `ACTIVE_POLICY_SOURCE`, remain authoritative for policy, and keep every Runtime effect disabled.

All referenced UTF-8 text records use canonical LF (`\n`) line endings for SHA-256 calculation. Checkout-specific CRLF materialization must not change a Bundle hash or Runtime verification result.

A Policy identity, version, path, hash, authority, fail-closed rule, or Runtime-effect mismatch blocks the replay before Worker invocation.

The Governance Policy binding verifies the exact policy source used by preflight. It does not activate Runtime, consume Approval, grant execution permission, or make the development replay authoritative.

## 4. Read-only Boundary

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

## 5. Integrity

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
Exact Governance Policy Binding
+
Exact Constraints
+
Created At
```

A changed Task, Binding, Assignment, Role, Registry, Governance Policy identity/version/reference/hash, I0.4 Index, constraint, or timestamp requires a new Bundle fingerprint.

## 6. Final Rule

> A valid Input Bundle is only an immutable development replay input. It grants no Runtime authority and cannot enable any external or mutating capability.
