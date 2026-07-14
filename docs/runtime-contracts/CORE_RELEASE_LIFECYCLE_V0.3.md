# Core Release Lifecycle v0.3 — Lean

**Status:** Review Ready
**Owner:** Thomas
**Runtime Activation:** Disabled until an exact Release is approved and the Approval, Activation, and Current pointer are committed.

## 1. Purpose

Preserve one exact Thomas Core Release and control how it becomes available to new Runtime Tasks without duplicating Review and Approval concepts.

```text
Semantic Core Sources
↓
Self-Contained Release Snapshot
↓
REVIEW_CORE_RELEASE.yaml
↓
Human / PR Review
↓
Runtime-Authoritative Approval
↓
Approval Commit
↓
Activation or Rollback Event + Current Pointer
↓
Single Commit
↓
Core Context Binding v0.3
↓
New Task Revision
```

Core Approval and Core Activation do not grant execution Permission.

## 2. Self-Contained Release

Each Release stores its own semantic artifact and validation snapshots.

```text
THOMAS_CORE/releases/<release_id>/
├── manifest.yaml
├── artifacts/
│   └── exact semantic Core files
└── validation_toolchain/
    ├── exact validator files
    ├── requirements-validation.lock
    └── validation_environment.yaml
```

Historical verification reads the Release directory, not the current working-tree Core or current validators.

`generated/docs/CORE_PROJECTION_MAP.yaml` is build-time validation metadata and is not part of the semantic Core Release bundle.

## 3. Review and Approval

Review is represented by:

```text
REVIEW_CORE_RELEASE.yaml
+
GitHub PR / operator review evidence
```

Approval has one meaning only:

> Thomas has approved one exact committed Release for Runtime Core reference.

Required external verification evidence:

```text
verified_by_control_channel

verified_by_protected_review

verified_by_signature
```

The Approval record preserves:

- Release ID.
- Core version.
- Core bundle SHA256.
- Manifest SHA256.
- Exact approved Git commit.
- External verification evidence.
- Permission boundary.

The CLI records and validates the supplied evidence structure. Authentication is performed by the referenced external control channel, protected review, or signature system.

## 4. Activation and Rollback

Activation and Current pointer update are one operator action and one commit.

```bash
python scripts/activate_core_release.py \
  --activation-type activate \
  --manifest THOMAS_CORE/releases/<release_id>/manifest.yaml \
  --approval THOMAS_CORE/approvals/<approval_id>.yaml \
  ...
```

The command:

1. Verifies the Release and committed Approval.
2. Creates one immutable Activation record.
3. Atomically updates `CURRENT_CORE_RELEASE.yaml`.
4. Requires both files to be committed before Runtime use.

Rollback uses the same command:

```bash
python scripts/activate_core_release.py \
  --activation-type rollback \
  ...
```

Rollback creates a new immutable event. It does not rewrite historical Releases, Approvals, Activations, or Task Bindings.

## 5. Fail-Closed Deactivation

```bash
python scripts/deactivate_core_release.py \
  ...
```

The command:

1. Verifies the committed Current pointer.
2. Creates one immutable Deactivation record.
3. Atomically changes the Current pointer to:

```yaml
runtime_activation_status: deactivated_fail_closed
```

The Deactivation record and Current pointer are committed together.

While deactivated:

- New Core Context Bindings are blocked.
- Existing Task Bindings are not silently changed.
- Recovery requires a verified Activation or Rollback event.

## 6. Revocation

Revocation is an immutable effective record.

```text
Approval or Activation
↓
revoke_core_approval.py
↓
THOMAS_CORE/revocations/<revocation_id>.yaml
```

An effective Revocation invalidates the target for new Runtime reference.

When a revoked target is Current, the operator must deactivate fail closed or roll back to another verified Release.

## 7. Current Pointer

`CURRENT_CORE_RELEASE.yaml` is the only mutable Runtime pointer.

Active:

```yaml
runtime_activation_status: approved_via_activation_registry

activation_id:
activation_path:
activation_sha256:
release_id:
approval_id:
```

Fail closed:

```yaml
runtime_activation_status: deactivated_fail_closed

deactivation_id:
deactivation_path:
deactivation_sha256:
```

The pointer references one immutable lifecycle event.

Runtime use requires the pointer and referenced event to be committed and verified.

## 8. Core Context Binding

```text
CURRENT_CORE_RELEASE.yaml
↓
Activation
↓
Approval
↓
Self-Contained Release Manifest
↓
Core Context Binding v0.3
```

The Binding stores minimal lineage references instead of copying Core artifact hashes.

Rule membership is resolved from the bound Release Manifest.

## 9. Safety Boundaries

```text
Core Approval
≠
Execution Permission

Core Activation
≠
External Action Permission

Core Binding
≠
Tool or Program Permission

Rollback
≠
Existing Task Rebind

Learning
≠
Approved Release Mutation
```

## 10. Final Principle

> Release snapshots preserve the past.

> Review and Approval are separate concepts.

> Approval has one Runtime-authoritative meaning.

> Activation and Current pointer update are one auditable operation and one commit.

> Permission remains separate.
