# THOMAS AGENT — STEP 4: PHYSICAL ARCHITECTURE SEPARATION PLAN

**Status:** Migration Plan
**Baseline:** `THOMAS_AGENT_I0_5_5_PRE_SLIMMING`
**Depends on:**
- Step 1 Architecture Slimming Principles
- Step 2 Artifact Classification
- Step 3 Single Source of Truth Finalization

---

## 1. Step 4 Objective

Step 4 defines the physical separation of the repository into:

```text
active/
generated/
deferred/
archive/
```

This step does not immediately delete or rewrite the entire repository.

The purpose is to:

- separate current authoritative architecture from future design;
- remove generated evidence from active authority;
- isolate historical documents;
- prevent deferred contracts from participating in active validation;
- prepare a safe, reversible migration;
- preserve all existing safety boundaries during restructuring.

Core migration rule:

> Separate first. Deduplicate second. Delete last.

---

## 2. Target Repository Shape

The target structure is:

```text
thomas_agent/
├─ governance/
│  ├─ SYSTEM_CONSTITUTION.md
│  ├─ GOVERNANCE_POLICY.yaml
│  └─ MEMORY_POLICY.yaml
│
├─ core/
│  ├─ MVP_ACTIVE_CORE.yaml
│  ├─ CURRENT_CORE_RELEASE.yaml
│  └─ releases/
│
├─ contracts/
│  ├─ TASK_CONTRACT.yaml
│  ├─ ROLE_ASSIGNMENT_CONTRACT.yaml
│  ├─ AGENT_OUTPUT_CONTRACT.yaml
│  ├─ VALIDATION_RESULT_CONTRACT.yaml
│  ├─ AUDIT_EVENT_CONTRACT.yaml
│  └─ schemas/
│
├─ roles/
│  ├─ THOMAS_PRIME_CHARTER.md
│  ├─ ROLE_REGISTRY.yaml
│  ├─ ACTIVE/
│  └─ CANDIDATES/
│
├─ programs/
│  ├─ PROGRAM_REGISTRY.yaml
│  └─ definitions/
│
├─ tools/
│  ├─ TOOL_REGISTRY.yaml
│  └─ definitions/
│
├─ runtime/
│  ├─ kernel/
│  ├─ policy/
│  ├─ router/
│  ├─ workers/
│  ├─ validation/
│  ├─ memory/
│  └─ audit/
│
├─ tests/
│  ├─ active/
│  ├─ shared/
│  └─ fixtures/
│
├─ generated/
│  ├─ release_gate/
│  ├─ fingerprints/
│  ├─ manifests/
│  └─ reports/
│
├─ deferred/
│  ├─ runtime_entry/
│  ├─ executor/
│  ├─ operations/
│  ├─ control_channel/
│  └─ sandbox/
│
├─ archive/
│  ├─ architecture/
│  ├─ contracts/
│  ├─ releases/
│  └─ review_evidence/
│
├─ scripts/
│  ├─ active_gate/
│  ├─ deferred_validation/
│  └─ migration/
│
└─ docs/
   ├─ README.md
   ├─ ARCHITECTURE.md
   └─ ROADMAP.md
```

---

## 3. Migration Categories

Every artifact must receive exactly one primary classification.

### 3.1 ACTIVE_NORMATIVE

Defines current rules or canonical runtime records.

Examples:

- Thomas Core
- System Constitution
- Governance Policy
- Task Contract
- Role Definition
- Role Registry status metadata
- Program Registry status metadata
- Tool Registry status metadata
- Validation Result Contract
- Audit Event Contract

### 3.2 ACTIVE_IMPLEMENTATION

Implements the current active architecture.

Examples:

- Runtime Kernel
- Policy evaluator
- Router
- Worker interface
- Validation engine
- Memory engine
- Audit writer
- active release gate

### 3.3 GENERATED

Produced from source files or validation runs.

Examples:

- release gate evidence
- source fingerprints
- generated manifests
- generated indexes
- validation reports
- release build outputs

### 3.4 DEFERRED

Future design that is intentionally not part of the current runtime.

Examples:

- runtime-authoritative entry
- exact entry authorization
- approval consumption
- durable CAS
- crash recovery
- executor
- monitoring
- alerting
- health
- clock sync
- kill switch
- supervisor
- scheduler
- sandbox

### 3.5 HISTORICAL

Superseded architecture, legacy contracts, prior release evidence, and historical review documents.

### 3.6 DUPLICATE_CANDIDATE

Artifacts that currently repeat rules owned by another Source of Truth.

A duplicate candidate is not deleted immediately. It is first converted to:

- reference documentation;
- generated projection;
- compatibility shim;
- deferred requirement;
- historical record.

---

## 4. Migration Table — Top-Level Families

| Current Path | Target Path | Classification | Source of Truth After Move | Action |
|---|---|---:|---:|---|
| `THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | `core/MVP_ACTIVE_CORE.yaml` | ACTIVE_NORMATIVE | Yes | Move with compatibility reference |
| `THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` | `core/CURRENT_CORE_RELEASE.yaml` | ACTIVE_NORMATIVE | Yes | Move if present |
| `THOMAS_CORE/releases/` | `core/releases/` | MIXED | Manifest only | Separate source manifest from copied snapshots |
| `03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md` | `roles/THOMAS_PRIME_CHARTER.md` | ACTIVE_NORMATIVE | Yes | Move |
| `03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` | `roles/DYNAMIC_ROLE_BASE.md` | ACTIVE_NORMATIVE | Limited | Retain as common role base |
| `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` | `roles/ROLE_REGISTRY.yaml` | DUPLICATE_CANDIDATE | Status/index only | Slim fields before final activation |
| `03_ROLE_CONTRACTS/ROLES/ACTIVE/` | `roles/ACTIVE/` | ACTIVE_NORMATIVE | Yes | Move |
| `03_ROLE_CONTRACTS/ROLES/CANDIDATES/` | `roles/CANDIDATES/` | ACTIVE_NORMATIVE | Yes | Move |
| `05_REGISTRIES/PROGRAM_REGISTRY.yaml` | `programs/PROGRAM_REGISTRY.yaml` | ACTIVE_NORMATIVE | Status/index only | Slim governance duplication |
| `05_REGISTRIES/TOOL_REGISTRY.yaml` | `tools/TOOL_REGISTRY.yaml` | ACTIVE_NORMATIVE | Status/index only | Slim governance duplication |
| `docs/runtime-contracts/` | Split by table below | MIXED | Mixed | Family separation required |
| `schemas/` | `contracts/schemas/` plus deferred/archive schemas | MIXED | Only active record schemas | Split by contract class |
| `runtime/read_only_kernel/` | `runtime/kernel/` and shared modules | ACTIVE_IMPLEMENTATION | Yes | Refactor after move |
| `runtime/read_only_entry/` | `deferred/runtime_entry/implementation_candidate/` | DEFERRED | No | Move out of active runtime |
| `runtime/protected_governance_state/` | `deferred/runtime_entry/protected_state_candidate/` | DEFERRED | No | Move |
| `build/release_gate/` | `generated/release_gate/` | GENERATED | No | Move |
| release snapshots containing copied source | `generated/` or GitHub Releases | GENERATED | No | Stop treating copies as active source |
| legacy architecture docs | `archive/architecture/` | HISTORICAL | No | Move with index |
| phase review evidence | `archive/review_evidence/` or `deferred/**/evidence/` | HISTORICAL/DEFERRED | No | Split by future relevance |
| phase-specific validators | `scripts/deferred_validation/` | DEFERRED | No | Remove from active gate |
| active core/runtime validators | `scripts/active_gate/` | ACTIVE_IMPLEMENTATION | Yes | Consolidate later |

---

## 5. Runtime Contract Family Classification

### 5.1 Keep Active

The following contract families remain in the active architecture.

| Current Contract | Target | Reason |
|---|---|---|
| `TASK_CONTRACT_V0.3.md` | `contracts/TASK_CONTRACT.md` | Canonical unit of work |
| `TASK_STATE_MACHINE_V0.1.yaml` | `contracts/TASK_STATE_MACHINE.yaml` | Active lifecycle |
| `CORE_CONTEXT_BINDING_V0.3.md` | `contracts/CORE_CONTEXT_BINDING.md` | Core lineage |
| `AGENT_OUTPUT_CONTRACT_V0.2.md` | `contracts/AGENT_OUTPUT_CONTRACT.md` | Runtime result |
| `ROLE_ASSIGNMENT_CONTRACT.md` | `contracts/ROLE_ASSIGNMENT_CONTRACT.md` | Task-to-role binding |
| `VALIDATION_RESULT_CONTRACT_V0.1.md` | `contracts/VALIDATION_RESULT_CONTRACT.md` | Independent validation record |
| `AUDIT_EVENT_CONTRACT_V0.1.md` | `contracts/AUDIT_EVENT_CONTRACT.md` | Append-only evidence |
| `EXECUTION_BUDGET_SCHEMA.yaml` | `contracts/EXECUTION_BUDGET.yaml` | Resource ceiling |
| `PROGRAMIZATION_REVIEW_POLICY_V0.1.md` | `governance/PROGRAMIZATION_POLICY.md` | Candidate creation only |
| Core lifecycle contracts | `core/lifecycle/` | Core versioning and activation |

### 5.2 Consolidate into Governance

The following files currently contain overlapping rules.

| Current Artifact | Future Role |
|---|---|
| `AUTHORITY_AND_PERMISSION_MODEL.md` | Human-readable governance explanation |
| `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md` | Merge principles into Constitution/Governance docs |
| `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` | Migrate rules into `GOVERNANCE_POLICY.yaml` |
| `PERMISSION_DECISION_CONTRACT_V0.3.md` | Retain only decision record schema |
| `APPROVAL_CONTRACT_V0.1.md` | Retain only approval lifecycle record |
| `ACTION_FINGERPRINT_POLICY_V0.1.md` | Move deterministic identity rule into Governance shared rule |

Target:

```text
governance/
├─ SYSTEM_CONSTITUTION.md
├─ GOVERNANCE_POLICY.yaml
└─ GOVERNANCE_REFERENCE.md

contracts/
├─ PERMISSION_DECISION_CONTRACT.yaml
└─ APPROVAL_RECORD_CONTRACT.yaml
```

### 5.3 Move to Deferred Runtime Entry

| Current Contract Family | Target |
|---|---|
| Runtime Promotion Readiness | `deferred/runtime_entry/readiness/` |
| Runtime-Authoritative Entry Plan | `deferred/runtime_entry/plan/` |
| Disabled Runtime Entry Adapter | `deferred/runtime_entry/adapter/` |
| Exact Entry Authorization | `deferred/runtime_entry/authorization/` |
| At-Most-Once Transition | `deferred/runtime_entry/transition/` |
| Protected Governance State | `deferred/runtime_entry/state/` |
| Durable CAS | `deferred/runtime_entry/state/` |
| Crash Recovery | `deferred/runtime_entry/recovery/` |
| Disabled Single Entry Integration | `deferred/runtime_entry/integration/` |
| I0.5.1–I0.5.5 Boundary documents | `deferred/runtime_entry/boundaries/` |

### 5.4 Move to Deferred Executor

| Current Contract Family | Target |
|---|---|
| Executor Registry Design | `deferred/executor/registry/` |
| Executor Readiness Review | `deferred/executor/readiness/` |
| Disabled Restricted Execution Service | `deferred/executor/interface/` |
| Hot-Path Revalidation | `deferred/executor/pre_execution/` |
| Approval Consumption Preview | `deferred/executor/approval_consumption/` |
| Executor Candidate Intake | `deferred/executor/intake/` |
| Executor Candidate Intake Review | `deferred/executor/intake/` |
| Execution Request/Result preview-only forms | `deferred/executor/records/` |

### 5.5 Move to Deferred Operations

| Current Contract Family | Target |
|---|---|
| Monitoring Snapshot | `deferred/operations/monitoring/` |
| Alert Event | `deferred/operations/alerting/` |
| Health Snapshot | `deferred/operations/health/` |
| Clock Sync Evidence | `deferred/operations/clock/` |
| Rollback / Recovery | `deferred/operations/recovery/` |
| Monitoring Threshold Policy | `deferred/operations/thresholds/` |
| Threshold Evaluation | `deferred/operations/thresholds/` |
| Disabled Process Supervisor | `deferred/operations/supervisor/` |
| Disabled Scheduler | `deferred/operations/scheduler/` |

### 5.6 Move to Deferred Control Channel

| Current Contract Family | Target |
|---|---|
| Control Channel Identity Binding | `deferred/control_channel/identity/` |
| Control Channel Command Envelope | `deferred/control_channel/commands/` |
| Kill Switch State | `deferred/control_channel/kill_switch/` |
| Kill Switch Command Review | `deferred/control_channel/kill_switch/` |

### 5.7 Move to Deferred Sandbox

| Current Contract Family | Target |
|---|---|
| Local Reversible Sandbox Test Plan | `deferred/sandbox/plans/` |
| Local Reversible Sandbox Test Review | `deferred/sandbox/reviews/` |

---

## 6. Schema Migration Rules

Schemas are split according to the contract they validate.

### 6.1 Active Schemas

Keep active only when the object is:

- stored by the current runtime;
- exchanged between current runtime modules;
- required for active lineage;
- required for active validation.

Expected active schema families:

```text
task
task_state
core_context_binding
role_assignment
agent_output
permission_decision
approval_record
execution_budget
validation_result
audit_event
core_release_lifecycle
programization
operational_knowledge
```

### 6.2 Deferred Schemas

Schemas for future runtime entry, executor, monitoring, scheduler, control channel, or sandbox move with their contract families.

### 6.3 Historical Schemas

Legacy versions remain in:

```text
archive/contracts/schemas/
```

They may be used only for migration or old-record validation.

### 6.4 Compatibility Schema Index

Create:

```text
contracts/schemas/SCHEMA_INDEX.yaml
```

Example:

```yaml
schemas:
  task.v0.3:
    status: active
    path: task.v0.3.schema.json

  runtime_entry_authorization.v0.1:
    status: deferred
    path: ../../deferred/runtime_entry/schemas/runtime_entry_authorization.v0.1.schema.json

  task.v0.2:
    status: historical
    path: ../../archive/contracts/schemas/task.v0.2.schema.json
```

The index records location and status but does not redefine schema content.

---

## 7. Validator and Release Gate Separation

The current release gate validates active runtime, deferred executor, operations, control, sandbox, and I0.5.1–I0.5.5 as one mandatory chain.

This must be split.

### 7.1 Active Gate

Target command:

```text
python scripts/run_active_gate.py
```

Active checks:

1. static integrity;
2. Thomas Core;
3. Constitution and Governance Policy;
4. Task and Core Binding;
5. Role Definition and Registry index;
6. Program and Tool Registry index;
7. Runtime Kernel import and deterministic tests;
8. Validation;
9. Audit;
10. schema parity for active records;
11. security and secret exclusion.

### 7.2 Deferred Architecture Gate

Target command:

```text
python scripts/run_deferred_architecture_gate.py
```

This checks deferred design documents only when explicitly requested.

It must not block normal active MVP development unless a deferred artifact is modified.

### 7.3 Historical Validation

Historical compatibility tests run separately:

```text
python scripts/run_legacy_compatibility_gate.py
```

### 7.4 Generated Evidence

Each gate writes only to:

```text
generated/release_gate/
```

Evidence is ignored as a source input by the active gate.

---

## 8. Compatibility Strategy

Physical moves can break paths, hashes, imports, and release manifests.

Therefore migration occurs in two passes.

### Pass A — Compatibility Bridge

For every moved authoritative artifact:

- create the new canonical path;
- leave a compatibility stub or redirect at the old path;
- update registries to new paths;
- update validators to accept the new canonical path;
- preserve legacy record validation;
- run both old and new path checks temporarily.

Compatibility stub example:

```markdown
# Moved

This document is no longer authoritative.

Canonical source:

`../../governance/GOVERNANCE_POLICY.yaml`

Status: compatibility reference only.
```

For YAML/JSON files, use a migration index rather than invalid redirect syntax.

### Pass B — Legacy Path Retirement

After one stable release:

- remove old path from active validators;
- move compatibility stubs to archive;
- preserve migration mapping;
- update release documentation;
- regenerate fingerprints.

---

## 9. Deletion Policy

Step 4 performs no broad deletion.

A file may be deleted only if all conditions are true:

```yaml
deletion_gate:
  content_is_generated_duplicate: true
  canonical_source_exists: true
  no_runtime_imports: true
  no_registry_reference: true
  no_validator_dependency: true
  no_historical_record_dependency: true
  migration_mapping_recorded: true
  focused_tests_pass: true
```

Otherwise the file is moved, archived, or replaced by a compatibility reference.

---

## 10. Migration Execution Order

The physical migration should be executed in the following order.

### Step 4A — Create New Empty Structure

Create:

```text
governance/
core/
contracts/
roles/
programs/
tools/
generated/
deferred/
archive/
scripts/active_gate/
scripts/deferred_validation/
scripts/migration/
```

No files moved yet.

### Step 4B — Move Non-Authoritative Artifacts First

Move:

- generated release evidence;
- build reports;
- source fingerprints;
- historical review evidence.

This has the lowest runtime risk.

### Step 4C — Move Deferred Families

Move:

- I0.5.1–I0.5.5 runtime entry;
- executor;
- operations;
- control channel;
- sandbox.

Remove them from the active gate but preserve a deferred validation gate.

### Step 4D — Establish New Active Sources

Create or migrate:

- `SYSTEM_CONSTITUTION.md`
- `GOVERNANCE_POLICY.yaml`
- `MEMORY_POLICY.yaml`
- active contract directory
- role/program/tool registries

### Step 4E — Add Compatibility Layer

Preserve old paths temporarily and publish migration index.

### Step 4F — Split Release Gates

Create active, deferred, and compatibility gates.

### Step 4G — Validate

Run:

- active gate;
- deferred gate;
- legacy compatibility gate;
- path-reference scan;
- schema-reference scan;
- import check;
- secret scan;
- diff check.

---

## 11. Required Migration Index

Create:

```text
scripts/migration/ARTIFACT_MIGRATION_INDEX.yaml
```

Format:

```yaml
migrations:

  - artifact_id: governance_policy
    old_path: docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml
    new_path: governance/GOVERNANCE_POLICY.yaml
    classification: ACTIVE_NORMATIVE
    old_authoritative: true
    new_authoritative: true
    compatibility_action: reference_stub
    delete_old: false

  - artifact_id: i0_5_5_entry_integration
    old_path: docs/runtime-contracts/DISABLED_SINGLE_READ_ONLY_ENTRY_INTEGRATION_CANDIDATE_CONTRACT_V0.1.md
    new_path: deferred/runtime_entry/integration/DISABLED_SINGLE_READ_ONLY_ENTRY_INTEGRATION_CANDIDATE_CONTRACT_V0.1.md
    classification: DEFERRED
    old_authoritative: false
    new_authoritative: false
    compatibility_action: migration_index_only
    delete_old: false
```

---

## 12. Step 4 Acceptance Criteria

```yaml
step_4_acceptance:

  target_structure_defined: true
  top_level_migration_map_defined: true
  contract_family_classification_defined: true
  schema_migration_policy_defined: true
  active_gate_scope_defined: true
  deferred_gate_scope_defined: true
  compatibility_strategy_defined: true
  deletion_policy_defined: true
  execution_order_defined: true

  physical_move_started: false
  runtime_behavior_changed: false
  existing_safety_boundary_removed: false
```

---

## 13. Next Step

Step 5 will implement logical deduplication before or alongside physical migration.

Priority order:

1. Governance Policy consolidation;
2. Role Registry slimming;
3. Program Registry slimming;
4. Tool Registry slimming;
5. phase-specific validator consolidation;
6. Kernel responsibility split;
7. final path migration.

No new autonomous capability is introduced during Step 5.
