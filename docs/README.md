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
| Architecture review findings (why C is parked) | [Architecture Review Record](ARCHITECTURE_REVIEW_RECORD.md) |
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
- Live MVP Runtime: [`runtime/mvp_runtime/`](../runtime/mvp_runtime/)
- Registry resolver: [`runtime/registry_resolution.py`](../runtime/registry_resolution.py)
- Validation and tests: [`scripts/`](../scripts/), [`tests/`](../tests/)

## MVP Runtime (live agent)

The first live agent capability is built under [`runtime/mvp_runtime/`](../runtime/mvp_runtime/)
(the read-only Kernel is reused as libraries; it is not extended). It runs one request
end-to-end: intake → plan (classify/bind/permission/assign) → read-only search → specialist
model call → output validation → hash-chained audit → final response. Nothing is executed;
every record stays REVIEW_ONLY / EVIDENCE_ONLY, and network-capable capabilities are OFF
until locally activated behind the Safety-Flag Gate.

| Capability | Entry point / doc |
|---|---|
| Run the MVP intake CLI | `python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."` |
| Single source for authority levels / invariant / effect blocks | [`runtime/mvp_runtime/authority.py`](../runtime/mvp_runtime/authority.py) |
| Permission decisions (analysis + search) | [`PERMISSION_DECISION_CONTRACT_V0.3`](runtime-contracts/PERMISSION_DECISION_CONTRACT_V0.3.md), [`runtime/mvp_runtime/permission.py`](../runtime/mvp_runtime/permission.py) |
| Enforced Safety-Flag Gate (model / network OFF by default) | [`runtime/mvp_runtime/safety_gate.py`](../runtime/mvp_runtime/safety_gate.py); activate locally with [`scripts/activate_safety_flag.py`](../scripts/activate_safety_flag.py) |
| Durable append-only ledger | [`runtime/mvp_runtime/store.py`](../runtime/mvp_runtime/store.py) |
| R3 read-only web-search tool | [`READONLY_SEARCH_TOOL_V0.1`](runtime-contracts/READONLY_SEARCH_TOOL_V0.1.md), [`runtime/mvp_runtime/tools.py`](../runtime/mvp_runtime/tools.py) |
| R4 operator control channel (Telegram private 1:1) | [`OPERATOR_CONTROL_CHANNEL_V0.1`](runtime-contracts/OPERATOR_CONTROL_CHANNEL_V0.1.md), [`runtime/mvp_runtime/operator.py`](../runtime/mvp_runtime/operator.py); run `python -m runtime.mvp_runtime.operator_cli` |

Local per-machine setup (Core activation, safety-flag activation, the ledger) is described in
the repo-root `CLAUDE.md`; that state lives under the gitignored `.runtime_governance_state/`.

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
