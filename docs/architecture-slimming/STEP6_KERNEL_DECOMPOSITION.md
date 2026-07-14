# THOMAS AGENT — STEP 6: COMPATIBILITY PROJECTION AND KERNEL DECOMPOSITION

**Status:** Implementation Candidate
**Baseline:** `THOMAS_AGENT_I0_5_5_PRE_SLIMMING`
**Runtime-authoritative effect:** None
**External effect:** None

## 1. Objective

Step 6 reduces the Runtime Kernel from a policy-owning monolith into a thin orchestrator.

It also provides a temporary compatibility projection so existing Runtime consumers can continue reading the legacy Registry shape while canonical ownership moves to:

- Governance Policy;
- Role Definitions;
- Program Definitions;
- Tool Definitions;
- slim Registries.

## 2. Compatibility Projection

New module:

```text
runtime/compat/legacy_registry_projection.py
```

It may:

- load slim Registry entries;
- resolve canonical Definition files;
- verify Definition hashes;
- combine Definition data with non-authoritative status metadata;
- generate the old Registry shape in memory.

It may not:

- persist projected Registry files;
- become a Source of Truth;
- expand authority;
- enable Roles, Programs, or Tools;
- bypass missing Definition or hash mismatch.

Fail-closed conditions:

```text
missing definition
invalid definition type
definition path missing
hash mismatch
duplicated authoritative fields inside slim Registry
```

## 3. Kernel Responsibility Split

Target flow:

```text
loader
→ preflight
→ policy
→ router
→ worker port
→ validation
→ audit
→ assembler
```

### Loader

Owns:

- repository root resolution;
- immutable input copy;
- basic context construction.

Does not own policy or routing.

### Preflight

Owns:

- required record presence;
- read-only replay mode checks;
- no-effect checks;
- structural readiness.

Does not grant Permission.

### Policy

Owns:

- calling canonical Governance Policy;
- converting policy result into a Runtime decision;
- fail-closed boundary checks.

Does not define Governance rules.

### Router

Owns:

- consuming a valid Assignment;
- selecting the already-approved deterministic worker route.

Does not define Role capability.

### Worker Port

Owns:

- invoking the injected Worker interface.

Does not choose Permission or mutate Registry.

### Validation

Owns:

- output/task lineage validation;
- output status validation;
- validation evidence.

Validation does not grant Permission.

### Audit

Owns:

- append-only event construction;
- deterministic event hash.

Audit does not define allowed behavior.

### Assembler

Owns:

- final Run record assembly;
- completed/blocked status;
- explicit non-authoritative and no-effect flags.

### Orchestrator

Owns only execution order and data flow.

## 4. Compatibility Migration Sequence

```text
Slim Registry introduced
↓
Compatibility projection introduced
↓
Old Runtime consumes projection
↓
Runtime loaders migrate to Definitions directly
↓
Projection usage decreases
↓
Compatibility layer retired
```

The compatibility layer must have a removal condition.

```yaml
retirement_conditions:
  all_runtime_consumers_use_canonical_definitions: true
  all_validators_use_source_ownership_checks: true
  no_legacy_registry_field_reads: true
  one_stable_release_passed: true
```

## 5. Kernel Non-Goals

Step 6 does not:

- add model-provider invocation;
- activate document.reader;
- activate search.readonly;
- activate schema.validator;
- activate document.parser;
- create Runtime-authoritative entry;
- consume Approval;
- activate Executor;
- enable Scheduler;
- enable external or financial actions.

## 6. Test Plan

Focused tests should cover:

### Compatibility Projection

- valid slim Role Registry projection;
- missing Role Definition blocks;
- Definition hash mismatch blocks;
- prohibited duplicated Role fields block;
- valid Program Registry projection;
- valid Tool Registry projection;
- projection never marks itself authoritative.

### Kernel Modules

- loader rejects invalid repository root;
- preflight blocks missing records;
- preflight blocks requested effects;
- policy blocks unsupported disposition;
- policy blocks invalid Governance runtime boundary;
- router rejects incomplete Assignment;
- worker invocation remains injected and deterministic;
- validation detects Task mismatch;
- audit hash is deterministic;
- assembler explicitly reports non-authoritative status;
- orchestrator completes valid replay;
- orchestrator blocks invalid replay.

## 7. Acceptance Criteria

```yaml
step_6_acceptance:
  compatibility_projection_candidate_created: true
  projection_non_authoritative: true
  projection_fail_closed: true
  kernel_loader_separated: true
  kernel_preflight_separated: true
  kernel_policy_separated: true
  kernel_router_separated: true
  worker_port_separated: true
  validation_separated: true
  audit_separated: true
  assembler_separated: true
  orchestrator_is_thin: true
  runtime_behavior_expanded: false
  external_effect_enabled: false
```

## 8. Next Step

Step 7 should:

1. create focused tests for these modules;
2. create `run_active_gate.py`;
3. move I0.5.1–I0.5.5 validation out of the Active Gate;
4. retain deferred validation under a separate command;
5. compare old Kernel output and decomposed Kernel output for parity.
