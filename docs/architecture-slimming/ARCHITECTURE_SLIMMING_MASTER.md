# Thomas Agent Architecture Slimming Master

**Baseline:** I0.5.5
**Status:** COMPLETED_MIGRATION_RECORD
**Runtime impact:** None
**Authority expansion:** None
**Document responsibility:** Completed PR #6–#11 sequence, final acceptance, and preserved safety boundaries

## Objective

The migration replaced architecture growth by condition-specific artifacts with:

```text
Condition
  → Canonical Domain Rule
  → Existing Policy or Contract Engine
  → Existing Shared Test Harness
```

Core rule:

> One Concept = One Authority = One Source of Truth.

## Completed Sequence

| PR | Result |
|---|---|
| #6 Documentation and Gate Consolidation | One documentation responsibility map, one Gate matrix, one canonical Gate CLI |
| #7 Active Kernel In-place Decomposition | Existing read-only Kernel split by responsibility with public API and behavior parity |
| #8 Active Registry Direct Slimming | Active Role/Program/Tool Registries reduced to status/path/hash indexes |
| #9 Governance Atomic Consolidation | One active machine-readable Governance Policy |
| #10 Deferred Architecture Compression | One Deferred design index, five families, one Deferred harness |
| #11 Generated / Historical / Final Reference Cleanup | Generated and Historical boundaries, compatibility retirement, final one-screen reference |

## Final Authority Map

| Concept | Owner |
|---|---|
| Thomas identity, values, goals, Core rules | `THOMAS_CORE/` |
| Governance, Authority, Permission, Approval requirements, effects, action identity, conflict rules | `governance/GOVERNANCE_POLICY.yaml` |
| Role behavior | Role Definitions |
| Program behavior | Program Definitions |
| Tool behavior | Tool Definitions |
| Role/Program/Tool status and location | Active Registries |
| Current deterministic read-only Runtime | `runtime/read_only_kernel/` |
| Future capability requirements | `deferred/DEFERRED_ARCHITECTURE.yaml` |
| Reproducible outputs | `generated/GENERATED_ARTIFACT_INDEX.yaml` |
| Superseded and frozen evidence | `historical/HISTORICAL_ARTIFACT_INDEX.yaml` |

## Final Repository Boundary

```text
governance/     canonical Governance sources
THOMAS_CORE/    Core sources and immutable release evidence
runtime/        active read-only Runtime and Registry resolver
programs/       Program Definitions
tools/          Tool Definitions
03_ROLE_CONTRACTS/ and 05_REGISTRIES/  active capability indexes/contracts
docs/           current navigation and active references
deferred/       future design requirements, non-runtime-authoritative
generated/      reproducible evidence, non-authoritative
historical/     superseded/frozen evidence, non-authoritative
scripts/ tests/ schemas/ examples/     active validation and record support
```

## Retired Compatibility

The following parallel migration paths have zero active import consumers and are preserved only under `historical/compatibility/`:

- `runtime/compat/` legacy Registry projection;
- `runtime/kernel_slim/` parallel Kernel candidate;
- `runtime/read_only_kernel/slim_candidate.py`;
- Role, Program, and Tool `*_SLIM_CANDIDATE.yaml` references.

The canonical replacements are:

- `runtime/registry_resolution.py`;
- `runtime/read_only_kernel/`;
- active Role/Program/Tool Registries.

## Preserved Safety Boundaries

1. Thomas remains final approval authority.
2. Thomas Core cannot be silently modified, approved, activated, rebound, or promoted.
3. Runtime cannot expand its own Authority.
4. Validation may block but cannot grant Permission, Approval, Authority, activation, or execution.
5. Registries cannot define behavior or grant Permission.
6. Missing, stale, ambiguous, inconsistent, or hash-mismatched authority data fails closed.
7. Deferred components remain disabled until a separate approved activation process exists.
8. Generated evidence grants no authority.
9. Historical evidence grants no authority.
10. Release snapshots are immutable evidence, not current source.
11. Runtime-authoritative Entry, model invocation, Tool/Program execution, Approval consumption, Executor handoff, Scheduler/Control dispatch, external action, financial action, Permission expansion, Authority expansion, and Core activation remain disabled.

## Final Acceptance

```yaml
architecture_slimming_final_acceptance:
  one_governance_machine_readable_owner: true
  active_kernel_decomposed_in_place: true
  active_registries_index_only: true
  canonical_registry_resolver_active: true
  legacy_registry_projection_retired: true
  parallel_kernel_candidate_retired: true
  one_deferred_design_authority: true
  one_deferred_gate_harness: true
  generated_boundary_active: true
  historical_boundary_active: true
  release_manifests_separated_from_copied_snapshots_by_classification: true
  active_architecture_explainable_on_one_screen: true
  runtime_behavior_changed: false
  runtime_authority_added: false
  safety_boundary_removed: false
```

## Final Reference

Current architecture and validation commands are maintained only in [`../ACTIVE_ARCHITECTURE.md`](../ACTIVE_ARCHITECTURE.md).

Detailed Step 2–6 review evidence and PR implementation records are Historical and do not define current authority.
