# Role Contracts

**Status:** `MVP Role Structure v0.3`
**Owner:** `Thomas`

## 1. Purpose

This folder manages Thomas Prime's Charter, Dynamic Role common rules, Role Definitions, current Role status, and Task-specific Role Assignments.

Thomas Prime is not a Dynamic Role and does not inherit `MVP_DYNAMIC_ROLE_CONTRACT.md`.

## 2. Structure

```text
03_ROLE_CONTRACTS/
├── README.md
├── THOMAS_PRIME_CHARTER.md
├── MVP_DYNAMIC_ROLE_CONTRACT.md
├── ROLE_DEFINITION_TEMPLATE.yaml
├── ROLE_ASSIGNMENT_CONTRACT.md
├── ROLE_REGISTRY.yaml
└── ROLES/
    ├── ACTIVE/
    │   ├── GENERAL_SPECIALIST_ROLE.md
    │   └── VALIDATION_ROLE.md
    └── CANDIDATES/
        ├── RESEARCH_ROLE.md
        ├── TRANSLATION_ROLE.md
        ├── CONTENT_ROLE.md
        ├── BUSINESS_ANALYSIS_ROLE.md
        └── DEVELOPMENT_ROLE.md
```

Related canonical Runtime contracts:

```text
docs/runtime-contracts/
├── CORE_CONTEXT_BINDING_V0.3.md
├── AUTHORITY_AND_PERMISSION_MODEL.md
├── AGENT_OUTPUT_CONTRACT_V0.2.md
├── EXECUTION_BUDGET_SCHEMA.yaml
└── RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.4.md
```

## 3. Responsibilities

| Document | Responsibility |
| --- | --- |
| `THOMAS_PRIME_CHARTER.md` | Prime identity, responsibility, authority boundary, and prohibited behavior |
| `MVP_DYNAMIC_ROLE_CONTRACT.md` | Common contract inherited by every Dynamic Role |
| `ROLE_DEFINITION_TEMPLATE.yaml` | Machine-readable Role Definition template |
| `ROLE_REGISTRY.yaml` | Current Role version, status, routability, lookup metadata, hashes, and Candidate trial policy |
| `ROLE_ASSIGNMENT_CONTRACT.md` | Exact Task-specific Role objective, capability subset, authority, permission, Memory, resources, validation, and budget |
| `ROLES/ACTIVE/` | Roles available for normal Runtime routing |
| `ROLES/CANDIDATES/` | Roles unavailable for normal routing; explicit isolated trial only |

## 4. Core Concept

```text
MVP Dynamic Role Contract
→ common rules

Role Definition
→ persistent capability and absolute ceiling

Role Registry
→ current status, version, routability, and lookup metadata

Role Assignment
→ exact Task-specific granted scope

Agent Output v0.2
→ traceable execution result
```

Role Definition alone never creates Runtime authority.

## 5. Authority and Permission

Authority Level and Permission Decision are separate.

```text
Authority
P0–P6
→ what action class an actor may perform

Permission Decision
ALLOW | EXECUTE_AND_REPORT | APPROVAL_REQUIRED | BLOCK
→ whether one exact action may proceed now
```

The canonical model is:

`../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md`

## 6. Role Status

Normal Runtime routing:

```text
active + routable: true
```

Candidate:

```text
candidate + routable: false
```

Candidate Roles are never selected by normal automatic routing.

An explicit Candidate trial is allowed only when all Registry trial requirements are satisfied.

Candidate trial does not activate or promote the Role.

## 7. Candidate to Active Promotion

```text
Repeated need
↓
Candidate Definition
↓
Exact-version isolated trial
↓
Independent validation
↓
Quality, cost, latency, failure, authority, and safety comparison
↓
Thomas approval
↓
Role Version and Registry update
↓
Active + routable
```

Minimum promotion requirements:

- Three or more similar completed Tasks show repeated need.
- The Role has distinct capability, quality, authority, Tool, Memory, or evaluation value.
- Candidate trial passes.
- No material authority or safety violation.
- Measurable improvement over General Specialist.
- Thomas approves the exact Role Version.
- Registry and Audit are updated.

## 8. Role Selection Flow

```text
Task
↓
Program sufficient?
├─ YES → Program path
└─ NO
   ↓
Registry active + routable lookup
↓
Definition hash and capability-set check
↓
Capability match
↓
Authority-level check
↓
Permission Decision check
↓
Numeric budget check
↓
Resource and validation check
↓
Role Assignment v0.2
↓
Role execution
↓
Agent Output v0.2
↓
Validation when required
↓
Prime integration
```

## 9. Runtime Read Order

### Startup

```text
CURRENT_CORE_RELEASE.yaml
↓
Approved Release Manifest and Approval
↓
CORE_METADATA.yaml
↓
MVP_ACTIVE_CORE.yaml
↓
MVP Operating Policy
↓
Runtime Contract Precedence Addendum
↓
Canonical Runtime Contracts
↓
Thomas Prime Charter
↓
MVP Dynamic Role Contract
↓
ROLE_REGISTRY.yaml
```

### Task Routing

```text
Task
↓
Core Context Binding v0.3
↓
Registry lookup
↓
Selected Role Definition and exact Version
↓
Definition hash and capability-set verification
↓
Authority, Permission, budget, resource, and validation checks
↓
Role Assignment v0.2
```

### Role Execution

```text
Role Assignment v0.2
↓
Exact Core Context Binding v0.3
↓
Exact Role Definition v0.3.0
↓
Assigned inputs, context, Active Core, Memory, resources, and budget
↓
Agent Output v0.2
```

## 10. Change Policy

- Role Assignment must preserve the Task's exact `core_context_binding_id`.
- A Role cannot select or upgrade the Core Release independently.
- A material Core rebind requires a new Task revision and new Assignment.

- Role Definition uses Semantic Versioning.
- Registry records the exact approved Role Version.
- Role Assignment pins one exact Role Version.
- Definition change does not retroactively affect a running Assignment.
- Capability, permission ceiling, status, routability, or contract hash change requires Registry update and Audit.
- Agent and Role cannot modify their own Definition, status, permission, resource allowlist, budget cap, or Registry record.


## Task and Core Binding Minimum

Task v0.3 is the minimum supported Task Contract.

Every Runtime Role Assignment preserves the exact `core_context_binding_id` from the Task revision.

The Role cannot choose a newer Core Release independently.

Exact Rule meaning is resolved through the bound Release snapshot.
