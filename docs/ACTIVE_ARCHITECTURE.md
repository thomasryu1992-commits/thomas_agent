# Thomas Agent Active Architecture

**Status:** Architecture Slimming sequence completed through PR #11; Post-Slimming Consistency Hardening through Fix #4
**Baseline:** I0.5.5
**Runtime-authoritative execution: Disabled**
**Document responsibility:** Final current architecture, authority ownership, repository boundaries, and canonical Gate entrypoints

## Architecture on One Screen

Active authority and execution lane:

```text
Thomas
  ↓
Thomas Core
  ↓
Governance Policy
  ↓
Thomas Prime
  ↓
Thin Read-only Runtime Kernel
  ↓
Router
  ↓
Role / Program / Tool Definitions
  ↓
Validation
  ↓
Memory Candidate / Append-only Audit
```

Inactive candidate lane — not part of the active dependency chain:

```text
System Constitution
  status: Migration Candidate
  authoritative: No
  active dependency: none
  proposed future position: between Thomas Core and Governance Policy
  cutover: separate review and explicit Thomas approval required
```

The active lane above is the only current authority and dependency chain. `governance/SYSTEM_CONSTITUTION.md` is not an active predecessor, policy source, or Runtime dependency. Its presence does not modify precedence, grant authority, or activate any capability.

Current execution path:

```text
Task
  → Core Context Binding
  → Governance / Permission Decision
  → Prime Planning and Routing
  → Deterministic Read-only Work
  → Validation
  → Result and Audit Evidence
```

The architecture is fail-closed when authority, lineage, source ownership, freshness, integrity, or policy interpretation is missing or ambiguous.

## Current Source of Truth

| Domain | Canonical owner |
|---|---|
| Organization design direction — Target / MVP / Dynamic Task Team layers, expansion and separation criteria, architecture guardrails (design authority only; grants no Permission or Runtime capability) | `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` |
| Identity, values, goals, Core rules | `THOMAS_CORE/` and Core lifecycle records |
| Authority, Permission, Approval requirements, effects, action identity, conflict rules | `governance/GOVERNANCE_POLICY.yaml` |
| Task and Runtime record boundaries | Active contracts under `docs/runtime-contracts/` and `schemas/` |
| Role behavior | Role Definition Markdown YAML front matter |
| Program behavior | `programs/definitions/*.yaml` |
| Tool behavior | `tools/definitions/*.yaml` |
| Role status and routability | `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` |
| Program status and enablement | `05_REGISTRIES/PROGRAM_REGISTRY.yaml` |
| Tool status and enablement | `05_REGISTRIES/TOOL_REGISTRY.yaml` |
| Active Runtime implementation | `runtime/read_only_kernel/` |
| Registry/Definition resolution | `runtime/registry_resolution.py` |
| Deferred design | `deferred/DEFERRED_ARCHITECTURE.yaml` |
| Generated classification | `generated/GENERATED_ARTIFACT_INDEX.yaml` |
| Historical classification | `historical/HISTORICAL_ARTIFACT_INDEX.yaml` |

A resolved Registry view is an in-memory consumer view. It is not persistent, authoritative, or permission-expanding.

### Non-authoritative Candidate Reference

| Candidate | Current status | Active dependency |
|---|---|---|
| `governance/SYSTEM_CONSTITUTION.md` | Migration Candidate; explicit cutover required | None |

The candidate Constitution is intentionally excluded from the current Source-of-Truth map. A future cutover must be reviewed separately, explicitly approved by Thomas, and applied atomically across the active architecture reference and validation boundary.

## Thin Runtime Kernel

```text
kernel facade
  → loader
  → preflight
  → policy adapter
  → router
  → worker port
  → validation
  → audit
  → assembler
```

`orchestrator.py` owns call order and data flow only. Governance owns policy. Definitions own capability behavior. Registries own status and location metadata only.

## Repository Boundaries

```text
Active
  governance/GOVERNANCE_POLICY.yaml  THOMAS_CORE/  roles/registries
  programs/  tools/  runtime/read_only_kernel/
  active contracts/schemas  tests  scripts

Candidate Reference
  governance/SYSTEM_CONSTITUTION.md
  migration candidate; no active authority or dependency

Deferred
  deferred/
  future Runtime Entry, Executor, Operations,
  Control Channel, Scheduler/Supervisor, Sandbox requirements

Generated
  generated/
  reproducible Gate evidence, fingerprints, locks, reports, projections

Historical
  historical/
  superseded architecture, frozen phase evidence,
  migration review records, retired compatibility implementations
```

**Generated evidence grants no authority.**

**Historical evidence grants no authority.**

Deferred design authority is not Runtime authority. Passing a Gate, producing a report, preserving a release snapshot, or retaining a candidate never activates a capability.

Core release manifests and their copied source/toolchain snapshots remain in `THOMAS_CORE/releases/` because their paths and hashes are immutable release evidence. The Historical index classifies those copies as non-current source without rewriting them.

## Canonical Gate Entrypoint

```bash
python scripts/run_architecture_gate.py --scope active --check-only
python scripts/run_architecture_gate.py --scope deferred --check-only
python scripts/run_architecture_gate.py --scope legacy --check-only
python scripts/run_architecture_gate.py --scope all --check-only
```

Repository-wide compatibility and release evidence:

```bash
python scripts/run_repository_release_gate.py --full --check-only
```

Compatibility wrapper commands may remain for external callers, but `scripts/gate_matrix.py` and `scripts/run_architecture_gate.py` own Gate composition.

### CI Scope Routing

CI routing selects an existing canonical Gate; it does not create authority or redefine Gate composition.

```text
Every pull request and main push
  → Active Gate

Deferred-owned path changed
  → Active Gate + Deferred Gate

Legacy-owned path changed
  → Active Gate + Legacy Gate

Shared CI / Gate infrastructure changed
  → Active + Deferred + Legacy + Full Repository Gate

Nightly schedule, manual dispatch, or release tag
  → Full Repository Gate on Ubuntu and Windows
```

`scripts/gate_matrix.py` owns the CI path classification patterns, and `scripts/classify_ci_scope_changes.py` only applies those patterns to the current Git diff. The Full Repository Gate remains the comprehensive integration and release check, but it is not the default blocking check for unrelated Active pull requests.

## Safety State

The following remain disabled:

```yaml
runtime_authoritative_entry_enabled: false
model_invocation_enabled: false
tool_execution_enabled: false
program_execution_enabled: false
network_access_enabled: false
filesystem_write_enabled: false
approval_consumption_enabled: false
executor_handoff_enabled: false
scheduler_dispatch_enabled: false
control_channel_dispatch_enabled: false
runtime_mutation_enabled: false
permission_expansion_enabled: false
authority_expansion_enabled: false
external_action_enabled: false
financial_action_enabled: false
core_activation_enabled: false
```

Policy authority, validation evidence, generated evidence, historical evidence, and Runtime execution authority are separate. None can silently grant another.

## Change Rule

Before creating a new Contract, Schema, Registry, Validator, Fixture, or Gate, determine whether the change is only a new condition within an existing canonical owner and shared harness.

> One Concept = One Authority = One Source of Truth.
