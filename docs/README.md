# Thomas Agent Documentation Index

**Status:** Navigation only
**Normative authority:** None

This file does not define Permission, Approval, Runtime behavior, readiness, or activation authority.

## Start Here

| Purpose | Document |
|---|---|
| Final active architecture and canonical Gate commands | [Active Architecture](ACTIVE_ARCHITECTURE.md) |
| Canonical Governance Policy | [`governance/GOVERNANCE_POLICY.yaml`](../governance/GOVERNANCE_POLICY.yaml) |
| Architecture Slimming principles | [Step 1 — Principles](architecture-slimming/STEP1_PRINCIPLES.md) |
| Completed PR #6–#11 migration record | [Architecture Slimming Master](architecture-slimming/ARCHITECTURE_SLIMMING_MASTER.md) |
| Deferred Architecture | [`deferred/README.md`](../deferred/README.md) |
| Generated Artifact boundary | [`generated/README.md`](../generated/README.md) |
| Historical Artifact boundary | [`historical/README.md`](../historical/README.md) |

## Non-active Candidate Reference

- System Constitution proposal: [`governance/SYSTEM_CONSTITUTION.md`](../governance/SYSTEM_CONSTITUTION.md)
  - Status: Migration Candidate
  - Authoritative: No
  - Active dependency: None
  - Future use requires separate review, explicit Thomas approval, and atomic cutover.

The candidate Constitution is intentionally excluded from the Active Source Families and from the current active authority lane.

## Active Source Families

- Core: [`THOMAS_CORE/`](../THOMAS_CORE/)
- Governance: [`governance/GOVERNANCE_POLICY.yaml`](../governance/GOVERNANCE_POLICY.yaml)
- Runtime contracts: [`docs/runtime-contracts/`](runtime-contracts/)
- Roles: [`03_ROLE_CONTRACTS/`](../03_ROLE_CONTRACTS/)
- Program Definitions and Registry: [`programs/`](../programs/), [`PROGRAM_REGISTRY.yaml`](../05_REGISTRIES/PROGRAM_REGISTRY.yaml)
- Tool Definitions and Registry: [`tools/`](../tools/), [`TOOL_REGISTRY.yaml`](../05_REGISTRIES/TOOL_REGISTRY.yaml)
- Active read-only Kernel: [`runtime/read_only_kernel/`](../runtime/read_only_kernel/)
- Registry resolver: [`runtime/registry_resolution.py`](../runtime/registry_resolution.py)
- Validation and tests: [`scripts/`](../scripts/), [`tests/`](../tests/)

## Boundary Indexes

- Deferred design: [`deferred/DEFERRED_ARCHITECTURE.yaml`](../deferred/DEFERRED_ARCHITECTURE.yaml)
- Generated artifacts: [`generated/GENERATED_ARTIFACT_INDEX.yaml`](../generated/GENERATED_ARTIFACT_INDEX.yaml)
- Historical artifacts: [`historical/HISTORICAL_ARTIFACT_INDEX.yaml`](../historical/HISTORICAL_ARTIFACT_INDEX.yaml)

Detailed classification, single-source review, separation planning, logical deduplication, Kernel candidate design, and PR implementation records are preserved under [`historical/architecture-slimming/`](../historical/architecture-slimming/). They are migration evidence, not current authority.

## Canonical Validation

```bash
python scripts/run_architecture_gate.py --scope active --check-only
python scripts/run_architecture_gate.py --scope deferred --check-only
python scripts/run_architecture_gate.py --scope legacy --check-only
python scripts/run_repository_release_gate.py --full --check-only
```

Passing validation, generated evidence, historical evidence, candidate status, or a merged architecture PR does not grant Runtime execution, Tool/Program enablement, Approval consumption, Executor handoff, external action, financial action, Permission expansion, Authority expansion, or Core activation.
