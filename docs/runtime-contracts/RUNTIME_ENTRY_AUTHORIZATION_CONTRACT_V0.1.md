# Runtime Entry Authorization Contract v0.1

**Schema:** `runtime_entry_authorization.v0.1`
**Phase:** `I0.5.3`
**Status:** `Review-only exact-entry authorization design`
**Owner:** `Thomas`

## Purpose

Bind one future Runtime-authoritative read-only entry attempt to one exact Task, Input Bundle, Current Core lineage, Core Context Binding, component implementation set, resource budget, output contract, nonce hash, and Action fingerprint.

This record is not Runtime permission, is not Runtime activation, and is never directly executable in I0.5.3.

## Precedence and reuse

I0.5.3 reuses rather than replaces:

- `RUNTIME_AUTHORITATIVE_READ_ONLY_ENTRY_PLAN_CONTRACT_V0.1.md` for Design/Activation Readiness and the bounded single-entry direction;
- `APPROVAL_CONTRACT_V0.1.md` for Thomas's exact Action Approval evidence under `permission_scope: RUNTIME_GOVERNANCE`;
- the existing Task, Core Context Binding, Runtime Input Bundle, Action Fingerprint, Validation, and Audit contracts.

This contract adds only the exact Runtime-entry binding and future at-most-once transition prerequisites.

## Required exact bindings

The record must bind:

- `task_id`, `task_revision`, and Task SHA-256;
- `input_bundle_id` and Input Bundle SHA-256;
- `current_core_release_id` and Core Bundle SHA-256;
- `core_context_binding_id` and Binding SHA-256;
- Kernel, Entry Planner, and Entry Adapter IDs, versions, and implementation SHA-256 values;
- a hash-only one-time nonce;
- issue time, expiration time, and a maximum 15-minute TTL;
- maximum Runtime duration, file-read count, and total bytes read;
- an exact list of repository-relative read paths with no glob, traversal, absolute path, duplicate, or symlink allowance;
- exact expected output schema IDs;
- a canonical Action fingerprint.

Any change requires a new Permission Decision, Action Approval, Entry Authorization, nonce, and Action fingerprint.

## Design decision evidence

The record may bind a Thomas design-direction decision, but that decision:

- approves only the future read-only Runtime foundation;
- approves at-most-one entry attempt;
- approves exact hash binding;
- keeps model, Tool, Program, network, domain/workspace/Core write, external, and financial effects disabled;
- grants no Runtime activation and no Runtime-entry permission.

Design-direction evidence is distinct from the Action Approval required for one exact entry attempt.

## Resource hard caps

```yaml
max_attempts: 1
max_ttl_seconds: 900
max_runtime_seconds: 60
max_files_read: 32
max_total_bytes_read: 8388608
```

A future Thomas approval may choose lower values but never exceed these caps without a new approved contract version. The selected limits must also be less than or equal to the exact Task execution budget bound by the Task hash.

## Action Approval boundary

The Action Approval must use the existing `approval.v0.1` contract and bind the exact Action fingerprint under `RUNTIME_GOVERNANCE`.

I0.5.3 does not create or verify a real Approval. It defines the exact fields that a future approved record must bind. `approval.v0.1` remains non-consumable by itself; the separate I0.5.3 atomic transition contract defines the future consumption boundary.

## Outcomes

```text
BLOCKED_NOT_READY
READY_FOR_THOMAS_ACTION_APPROVAL_REVIEW
APPROVED_NOT_CONSUMED_REVIEW_ONLY
```

All outcomes remain non-executable:

```yaml
usable_for_runtime_entry: false
runtime_entry_performed: false
```

## Non-goals

- no Approval creation or automatic approval;
- no real Approval verification;
- no compare-and-set;
- no nonce disclosure;
- no Runtime session;
- no Kernel call;
- no Tool, Program, model, network, external, or financial effect;
- no domain, workspace, Core, or governance-state write.
