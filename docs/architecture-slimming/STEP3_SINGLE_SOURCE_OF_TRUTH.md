# THOMAS AGENT — STEP 3: SINGLE SOURCE OF TRUTH FINALIZATION

**Status:** Draft for Architecture Slimming
**Baseline:** `THOMAS_AGENT_I0_5_5_PRE_SLIMMING`
**Purpose:** Define one authoritative owner and one Source of Truth for every core system concept before physical restructuring.

---

## 1. Step 3 Objective

The purpose of this step is not to merge every document into one file.

The purpose is to ensure that:

- each concept has one authoritative owner;
- each concept has one canonical Source of Truth;
- all other files reference the source instead of copying its rules;
- Runtime components execute policy but do not redefine policy;
- Registries index components but do not redefine component behavior;
- generated and review-only artifacts never become policy sources.

Core rule:

> One Concept = One Authority = One Source of Truth

---

## 2. Final Authority Layers

The Thomas Agent authority model is fixed as follows.

```text
Thomas
↓
Thomas Core
↓
System Constitution
↓
Governance Policy
↓
Thomas Prime
↓
Runtime Kernel
↓
Agents / Programs / Tools
↓
Validation
↓
Memory / Audit
```

### 2.1 Thomas

Thomas is the sovereign human authority.

Thomas owns:

- final approval of Thomas Core;
- final approval of System Constitution;
- approval of high-risk policy changes;
- approval of authority expansion;
- approval of activation of new autonomous capabilities.

No Agent, Program, Tool, Registry, Validator, or Runtime component may self-expand authority.

---

## 3. Single Source of Truth Map

### 3.1 Identity and Values

**Concept:** Who Thomas is, what Thomas values, and which strategic principles guide the system.

**Authority:** Thomas
**Source of Truth:**

```text
THOMAS_CORE/MVP_ACTIVE_CORE.yaml
```

**Allowed references:**

- Thomas Prime Charter
- Role Definitions
- Governance Policy
- Runtime Task Context
- Validation Rules

**Prohibited duplication:**

- Core values copied into Role Registry
- Core priorities copied into Task schemas
- Core rules redefined in Runtime code
- Core rules embedded independently in validators

---

### 3.2 System-Wide Constitutional Principles

**Concept:** Rules that apply to the entire system regardless of role, task, or runtime state.

**Authority:** Thomas
**Source of Truth:**

```text
governance/SYSTEM_CONSTITUTION.md
```

**Owns:**

- Thomas is final authority
- authority cannot self-expand
- no hidden execution
- no silent mutation of Core or policy
- no external action without appropriate permission
- validation does not grant permission
- memory does not grant authority
- future capability does not imply current permission

**Does not own:**

- operational decision matrices
- task-specific permissions
- role capabilities
- runtime state transitions

---

### 3.3 Risk, Permission, Approval, and Effects

**Concept:** Whether an action is allowed, blocked, review-only, or requires approval.

**Authority:** Governance Policy
**Source of Truth:**

```text
governance/GOVERNANCE_POLICY.yaml
```

**Owns:**

- effect classification
- risk classification
- permission decision rules
- approval requirement rules
- authority ceilings
- external action rules
- reversible vs irreversible effect rules
- control channel requirements
- authority expansion blocks

**Runtime records produced from this policy:**

```text
PermissionDecision
ApprovalRecord
```

These records store decisions and lifecycle evidence. They do not redefine the governing rules.

**Documents to demote from rule ownership:**

- `AUTHORITY_AND_PERMISSION_MODEL.md`
- `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md`
- `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml`
- `PERMISSION_DECISION_CONTRACT_V0.3.md`
- `APPROVAL_CONTRACT_V0.1.md`
- `ACTION_FINGERPRINT_POLICY_V0.1.md`

After slimming:

- human-readable explanation may remain;
- schemas may remain for records;
- rules must live only in `GOVERNANCE_POLICY.yaml`.

---

### 3.4 Task Structure and Lifecycle

**Concept:** The canonical unit of work.

**Authority:** Runtime Contract
**Source of Truth:**

```text
contracts/TASK_CONTRACT.yaml
```

**Owns:**

- task identity
- objective
- scope
- input references
- expected outputs
- task state
- task revision
- lifecycle
- routing request
- policy decision reference
- validation requirement
- audit reference

**Does not own:**

- permission rules
- role capabilities
- tool behavior
- program behavior
- memory policy

The Task references policy results. It does not recompute policy.

---

### 3.5 Role Capability and Restrictions

**Concept:** What a role can do and what limitations apply to the role.

**Authority:** Role Definition
**Source of Truth:**

```text
03_ROLE_CONTRACTS/ROLES/**/ROLE_DEFINITION.*
```

**Owns:**

- role purpose
- role capabilities
- role restrictions
- role permission ceiling
- required validation
- allowed tools/programs
- output expectations
- escalation conditions

**Does not own:**

- active status
- routability
- global permission policy
- approval policy

---

### 3.6 Role Status and Routing Index

**Concept:** Which roles exist, which version is active, and whether they are routable.

**Authority:** Role Registry
**Source of Truth:**

```text
03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml
```

**Registry is limited to:**

```yaml
role_id:
version:
status:
routable:
definition_path:
definition_sha256:
```

Optional metadata:

```yaml
owner:
role_type:
deprecated_at:
replacement_role_id:
```

**Registry must not repeat:**

- capabilities
- restrictions
- permission ceilings
- validation rules
- promotion policy
- tool allowlists
- program allowlists

These belong to Role Definition or Governance Policy.

---

### 3.7 Program Definition and Program Status

**Concept:** Deterministic reusable procedures.

**Authority split:**

- Program behavior: Program Definition
- Program status: Program Registry

**Sources of Truth:**

```text
programs/**/PROGRAM_DEFINITION.*
05_REGISTRIES/PROGRAM_REGISTRY.yaml
```

**Program Registry fields:**

```yaml
program_id:
version:
status:
enabled:
definition_path:
definition_sha256:
runtime_implementation_available:
```

Program Registry does not own global permission rules.

---

### 3.8 Tool Definition and Tool Status

**Concept:** External or internal capability interfaces.

**Authority split:**

- Tool behavior: Tool Definition
- Tool status: Tool Registry

**Sources of Truth:**

```text
tools/**/TOOL_DEFINITION.*
05_REGISTRIES/TOOL_REGISTRY.yaml
```

**Tool Registry fields:**

```yaml
tool_id:
version:
status:
enabled:
tool_class:
definition_path:
definition_sha256:
runtime_implementation_available:
```

Permission requirements must be referenced from Governance Policy, not copied.

---

### 3.9 Thomas Prime

**Concept:** Goal interpretation, decomposition, planning, routing, and coordination.

**Authority:** Thomas Prime Charter
**Source of Truth:**

```text
03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md
```

**Owns:**

- task interpretation
- planning
- route selection
- minimum sufficient team selection
- escalation
- coordination
- validation request

**Does not own:**

- global governance rules
- self-approval
- authority expansion
- Core mutation
- permission override
- executor activation

Thomas Prime coordinates the system. It does not govern the system.

---

### 3.10 Runtime Kernel

**Concept:** Execution of the approved task lifecycle.

**Authority:** Runtime implementation
**Source of Truth:**

```text
runtime/kernel/
```

**Runtime Kernel owns:**

- loading task context
- invoking Governance Policy
- invoking Router
- invoking Agent/Program/Tool
- collecting outputs
- invoking Validation
- writing approved Runtime Records
- emitting Audit Events

**Runtime Kernel must not own:**

- permission rules
- role capabilities
- approval rules
- Core rules
- Registry definitions
- monitoring thresholds
- future executor policy

The Kernel executes authoritative decisions but does not redefine them.

---

### 3.11 Validation

**Concept:** Independent or automatic review of outputs and process compliance.

**Authority:** Validation Engine
**Source of Truth:**

```text
runtime/validation/
contracts/VALIDATION_RESULT_CONTRACT.*
```

**Owns:**

- output validation
- evidence checks
- logic checks
- omission checks
- uncertainty checks
- policy compliance checks
- result classification

**Does not own:**

- permission grants
- approval grants
- task mutation
- Core mutation
- authority expansion

Validation may block progression but cannot grant new authority.

---

### 3.12 Memory

**Concept:** Working context and validated reusable knowledge.

**Authority:** Memory Policy
**Source of Truth:**

```text
governance/MEMORY_POLICY.yaml
```

**Owns:**

- working memory lifecycle
- validated memory candidate rules
- retention
- staleness
- provenance
- confidence
- review dates
- deprecation

Memory is evidence and context. It is not authority.

---

### 3.13 Audit

**Concept:** Append-only evidence of system decisions and transitions.

**Authority:** Audit Contract
**Source of Truth:**

```text
contracts/AUDIT_EVENT_CONTRACT.*
```

**Owns:**

- event identity
- actor
- action
- decision references
- before/after references
- timestamps
- hashes
- lineage

Audit records what happened. Audit does not decide what is allowed.

---

## 4. Generated Artifacts

Generated artifacts are never Sources of Truth.

Examples:

- release gate evidence
- source fingerprints
- generated manifests
- release snapshots
- generated indexes
- build reports
- test outputs

Rules:

```yaml
generated_artifacts:
  authoritative: false
  may_reference_source: true
  may_prove_validation: true
  may_grant_permission: false
  may_activate_runtime: false
```

Generated artifacts may provide evidence but never define policy.

---

## 5. Deferred Architecture

The following areas are preserved but removed from Active MVP authority.

```text
deferred/runtime_entry/
deferred/executor/
deferred/operations/
deferred/control_channel/
deferred/sandbox/
```

These may include:

- runtime-authoritative entry
- exact entry authorization
- approval consumption
- at-most-once transition
- durable CAS
- crash recovery
- executor readiness
- disabled executor interface
- monitoring
- alerting
- health checks
- clock sync
- kill switch
- process supervision
- scheduler
- sandbox execution

Deferred documents describe future requirements. They do not participate in the Active MVP decision chain.

---

## 6. Historical Architecture

Historical artifacts are retained for traceability but cannot participate in active decisions.

```text
archive/architecture/
archive/contracts/
archive/releases/
archive/review-evidence/
```

Historical artifacts:

- may explain prior decisions;
- may preserve compatibility context;
- may be cited in design history;
- may not be used as Runtime authority;
- may not be loaded by Active Registry or Runtime Kernel.

---

## 7. Conflict Resolution Rule

When two files appear to define the same concept:

1. use this Step 3 Authority Map;
2. identify the assigned Source of Truth;
3. keep the source authoritative;
4. convert the duplicate into:
   - reference documentation;
   - generated projection;
   - deferred requirement;
   - historical artifact;
5. remove duplicated rules from active validation.

No validator may preserve duplicate authority merely by checking that copies match.

---

## 8. Final Active Authority Map

```yaml
sources_of_truth:

  identity:
    source: THOMAS_CORE/MVP_ACTIVE_CORE.yaml
    authority: Thomas

  constitution:
    source: governance/SYSTEM_CONSTITUTION.md
    authority: Thomas

  governance:
    source: governance/GOVERNANCE_POLICY.yaml
    authority: Governance Policy

  task:
    source: contracts/TASK_CONTRACT.yaml
    authority: Runtime Contract

  role_definition:
    source: 03_ROLE_CONTRACTS/ROLES/**/ROLE_DEFINITION.*
    authority: Role Definition

  role_status:
    source: 03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml
    authority: Role Registry

  program_definition:
    source: programs/**/PROGRAM_DEFINITION.*
    authority: Program Definition

  program_status:
    source: 05_REGISTRIES/PROGRAM_REGISTRY.yaml
    authority: Program Registry

  tool_definition:
    source: tools/**/TOOL_DEFINITION.*
    authority: Tool Definition

  tool_status:
    source: 05_REGISTRIES/TOOL_REGISTRY.yaml
    authority: Tool Registry

  orchestration:
    source: 03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md
    authority: Thomas Prime

  runtime:
    source: runtime/kernel/
    authority: Runtime Kernel

  validation:
    source: runtime/validation/
    authority: Validation Engine

  memory:
    source: governance/MEMORY_POLICY.yaml
    authority: Memory Policy

  audit:
    source: contracts/AUDIT_EVENT_CONTRACT.*
    authority: Audit Contract
```

---

## 9. Step 3 Acceptance Criteria

```yaml
step_3_acceptance:

  one_concept_one_authority: true
  one_concept_one_source_of_truth: true
  governance_owner_defined: true
  runtime_boundary_defined: true
  registry_scope_reduced: true
  role_definition_scope_defined: true
  generated_artifacts_non_authoritative: true
  deferred_architecture_non_active: true
  historical_architecture_non_active: true
  physical_file_move_performed: false
  runtime_behavior_changed: false
```

---

## 10. Next Step

Step 4 will physically classify and move artifacts into:

```text
active/
generated/
deferred/
archive/
```

Before moving files, Step 4 must produce a migration table containing:

- current path;
- target path;
- classification;
- Source of Truth status;
- dependent files;
- validator impact;
- compatibility action;
- deletion status.

No file is deleted in Step 4 unless it is proven to be a pure generated duplicate.
