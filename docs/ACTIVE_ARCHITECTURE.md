# Thomas Agent Active Architecture

**Status:** Architecture Slimming sequence completed through PR #11; Post-Slimming Consistency Hardening through Fix #4. The live **MVP Runtime** (`runtime/mvp_runtime/`) has since been built on top of that baseline — R2–R10 plus the crypto (C-series), LLM-orchestration (M-series) and prediction-market (PM-series) increments.
**Structural baseline:** I0.5.5 — the read-only kernel and the contract/schema set. Unchanged; the MVP Runtime reuses it as libraries rather than extending it.
**Runtime-authoritative execution: Disabled** — the deferred Runtime-authoritative entry (`runtime/read_only_entry/`, `runtime/protected_governance_state/`) stays off. This does **not** mean nothing executes: the MVP Runtime executes, and it holds no standing runtime effect. See Safety State.
**Delivery history and open work:** what was built and why → `docs/BUILD_HISTORY.md`; what is left → `docs/REMAINING_WORK.md`. This document owns structure and ownership, not milestone status.
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
MVP Runtime  (runtime/mvp_runtime/ — the live executor; reuses the
Thin Read-only Runtime Kernel modules as libraries, never extends them)
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

Current execution path — the MVP Runtime, as implemented:

```text
Entry (intake CLI · operator/Telegram channel · scheduler · conversational front desk)
  → Task (intake.py)
  → Core Context Binding                       (binding.py — no run without an active approved Core)
  → Prime planning                             (prime.py: classify → select Role → PermissionDecision(s)
                                                → role_assignment; every record schema-validated)
  → [optional] importance triage                (triage.py — one budgeted gated model call)
  → [optional] read-only search                 (tools.py — INTERNAL_READ ALLOW; failure DEGRADES)
  → memory retrieval                            (memory.py — VALIDATED [V#] + CANDIDATE [M#] context)
  → specialist work                             (worker.py — the model call, behind the Safety-Flag Gate)
  → Validation                                  (validation.py automatic checks
                                                + [optional] validator.py independent reviewer,
                                                  stricter-wins; [optional] one bounded revision)
  → [optional] controlled write                 (workspace.py — EXECUTE_AND_REPORT, gated, create-only)
  → Memory Candidate + hash-chained Audit + durable ledger   (memory.py · audit.py · store.py)
```

Every optional step above is a separately governed action with its own PermissionDecision; none of
them widens the one the specialist runs under. Approval-bearing actions (memory promotion, candidate
Role trial, Program registration) leave this path and go through `approval.py` → operator decision →
`consumption.py`, single-use.

Current reach of the Router, stated so it is not inferred: `task.v0.3` models
`UNASSIGNED / PROGRAM / ROLE / HYBRID`, and the runtime emits only **ROLE** — no Program is enabled,
so no PROGRAM or HYBRID route is produced. The design direction that owns the wider route table is
`docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md` §8.4; what remains to build is
`docs/REMAINING_WORK.md`'s to say, not this document's.

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
| Active Runtime implementation — the live executor | `runtime/mvp_runtime/` |
| Deterministic read-only replay kernel — reused as libraries, never modified | `runtime/read_only_kernel/` |
| Per-machine capability grants (which gated capability this machine may use) | `runtime/mvp_runtime/safety_gate.py` + `.runtime_governance_state/safety_flag_activations/*.json` (local, gitignored) |
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

The kernel is **read-only and frozen**: it replays recorded bundles and is not modified. The MVP
Runtime imports its modules (`integrity`, `schema_validation`, `audit`, …) as libraries and keeps the
same discipline — `pipeline.py` owns call order, Governance owns policy, Definitions own behavior.

## MVP Runtime

The live executor. Same authority chain, one process, no kernel modification.

```text
Entry / control      cli.py · operator.py · scheduler.py · frontdesk.py · console_cli.py · control.py
Planning             intake.py · binding.py · planner.py · prime.py · assignment.py · triage.py
Governance surface   permission.py · authority.py · safety_gate.py · budgets.py
Work                 worker.py · providers.py · tools.py
Assurance            validation.py · validator.py · audit.py · store.py
Memory               memory.py · working_memory.py · retention.py
Governed asks        approval.py · approval_store.py · consumption.py · trial.py
Programization       programization.py · program_request.py · registration.py
Domain packages      crypto/ (C-series, incl. the gated live-order path)
                     predmarket/ (PM-series, observe-only)
```

Domain packages are applications of the same chokepoints, not parallel runtimes: they build the same
PermissionDecisions, pass the same Safety-Flag Gate, and append to the same audit chain.

## Repository Boundaries

```text
Active
  governance/GOVERNANCE_POLICY.yaml  THOMAS_CORE/  roles/registries
  programs/  tools/  runtime/mvp_runtime/  runtime/read_only_kernel/
  active contracts/schemas  tests  scripts

Local per-machine state (gitignored, never committed, does not travel via git)
  .runtime_governance_state/
  Core activation pointer, safety-flag grants, control state,
  ledger, schedules, registered budgets

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

Read this section as **two separate facts**, because conflating them is how this document previously
described shipped modules as absent:

1. **"Does the code exist?"** — owned by `docs/BUILD_HISTORY.md` and by the code itself.
2. **"May this machine act?"** — owned by the Governance Policy plus the per-machine grant. A good
   test result never answers question 2, and question 1 never answers it either.

### The policy grants no standing runtime effect

`governance/GOVERNANCE_POLICY.yaml` → `runtime_effect` is the authority, and every field in it is
false:

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  grants_runtime_execution: false
  grants_tool_or_program_enablement: false
  grants_external_execution: false
  grants_financial_execution: false
  grants_permission_expansion: false
  executor_handoff_allowed: false
  external_execution_allowed: false
  financial_execution_allowed: false
  runtime_mutation_allowed: false
  tool_enablement_allowed: false
  program_enablement_allowed: false
  permission_expansion_allowed: false
  approval_consumption_allowed: false
  core_activation_allowed: false
```

Two `*_implemented` flags in the same policy say the **code** exists, and deliberately grant nothing:
`approval_lifetime.approval_consumption_implemented: true` (R10) and
`financial.financial_transaction_execution_implemented: true` (LP4, 2026-07-25) — while
`financial.financial_executor_enabled` stays `false`.

### Implemented, and OFF until this machine is granted it

These capabilities are built. Each is refused unless a local, integrity-checked, TTL-capped,
Thomas-minted activation record enables that flag for that provider — an environment variable alone
fails closed, and deleting the record is a live revocation.

```yaml
model_invocation:      implemented; per-provider grant  (safety_gate.MODEL_INVOCATION)
network_access:        implemented; per-provider grant  (safety_gate.NETWORK_ACCESS)
filesystem_write:      implemented; per-provider grant  (safety_gate.FILESYSTEM_WRITE, R8 — create-only under workspace/)
approval_consumption:  implemented; per-provider grant  (safety_gate.APPROVAL_CONSUMPTION, R10 — single-use, scoped)
```

The same gate governs each data/venue provider on its own record (search backends, market data,
prediction-market venues, `live_trading`), so authorizing one never widens another.

### Structurally off — no code path, or no caller

```yaml
runtime_authoritative_entry: "deferred component; not the live path"
executor_handoff: "deferred component; the runtime acts directly (R8/R10 precedent)"
tool_execution: "no registered Tool is enabled; budget pins max_tool_calls to 0"
program_execution: "no registered Program is enabled; budget pins max_program_calls to 0; unregistered or disabled resource execution stays BLOCK"
runtime_mutation: false
permission_expansion: false
authority_expansion: false
core_activation_as_effect: "false — activation is an out-of-band operator step, per machine"
autonomous_financial_action: "the order path exists; no autonomous entry point may import it, enforced by test rather than by convention"
```

For whether **this** machine can place a live order, ask the computed board rather than this
document — `python -m runtime.mvp_runtime.crypto.live_readiness`. Prose drifts; the board is derived.

Policy authority, validation evidence, generated evidence, historical evidence, and Runtime execution authority are separate. None can silently grant another.

## Change Rule

Before creating a new Contract, Schema, Registry, Validator, Fixture, or Gate, determine whether the change is only a new condition within an existing canonical owner and shared harness.

> One Concept = One Authority = One Source of Truth.
