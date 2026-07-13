# Core Context Binding Contract v0.3 — Lean

**Status:** Review Ready
**Owner:** Thomas

## Purpose

Bind one Task revision to one exact approved and active Thomas Core Release without duplicating the Release Manifest.

```text
Task Record
↓
Current Core Activation
↓
Approved Release Manifest
↓
Explicit Task Rule Set
↓
Core Context Binding v0.3
↓
Task update
```

The Binding identifies Core lineage only and does not grant execution, Tool, Program, external, or financial Permission.

## Required Record

```yaml
schema_version: core_context_binding.v0.3

identity:
  core_context_binding_id:
  task_id:
  task_revision:
  trace_id:

release:
  release_id:
  core_version:
  manifest_path:
  manifest_sha256:
  approval_id:
  activation_id:

rules:
  loaded_rule_ids: []

binding:
  bound_at_utc:
  bound_by:
  binding_reason:

inheritance:
  child_tasks_inherit_binding: true
  assignments_reference_binding: true
  outputs_reference_binding: true

lineage:
  previous_binding_id:
  change_type:
  change_reason:
  material_change_ref:

rebind_policy:
  silent_mid_task_rebind_allowed: false
  explicit_task_revision_required: true
  replan_required: true
  reauthorization_required: true
```

## Why Artifact Hashes Are Not Repeated Here

The Release snapshot and its Manifest already store the exact Core artifact paths and hashes.

The Activation record already identifies the approved Release and Approval.

The Binding therefore stores only the references needed to resolve that chain.

```text
Binding
↓
Activation
↓
Approval
↓
Manifest
↓
Immutable Release artifacts
```

This avoids copying philosophy, Active Core, Projection, and Policy hashes into every Task Binding.

## Rule Resolution

```text
Task Rule IDs
=
Binding loaded_rule_ids
⊆
Bound Release active_rule_ids
```

Runtime resolves the active Rule set from the bound Release Manifest. The current working-tree Active Core is not the historical Runtime source of truth.

## Task Source

Binding creation reads Task ID, revision, trace ID, requested Rule IDs, existing Binding, and lifecycle status from the actual Task file.

A Task may keep `core_context_binding_id: null` only while `lifecycle.status: RECEIVED`.

## Lineage

Root Task:

```yaml
lineage:
  previous_binding_id: null
  change_type: root_binding
  change_reason: null
  material_change_ref: null
```

Later revision:

```yaml
lineage:
  previous_binding_id: ccb-previous
  change_type: task_revision_same_core
  change_reason: Material Task revision.
  material_change_ref: change-001
```

Core rebind:

```yaml
lineage:
  previous_binding_id: ccb-previous
  change_type: core_rebind
  change_reason: Approved Core Release changed.
  material_change_ref: core-change-001
```

A running Task never silently rebinds.

## Fail Closed

Block when:

- Current Core is deactivated.
- Activation, Approval, Manifest, or Release verification fails.
- Approval is revoked.
- Task identity or Rule set is invalid.
- Requested Rule is not active in the bound Release.
- Task already references a different Binding.
- Revision lineage is incomplete.
- Any input or output path escapes the Repository root.

## Final Rule

> A Binding is a minimal immutable lineage pointer, not a second Release Manifest.
