# Role Contracts

**Status:** `MVP Role Structure v0.3`  
**Owner:** `Thomas`

## 1. Purpose

This folder defines Thomas Prime, the common rules for Dynamic Roles, persistent Role Definitions, current Role Registry state, and Task-specific Role Assignments.

Thomas Prime is not a Dynamic Role and does not inherit `MVP_DYNAMIC_ROLE_CONTRACT.md`.

## 2. Structure

```text
03_ROLE_CONTRACTS/
|-- README.md
|-- THOMAS_PRIME_CHARTER.md
|-- MVP_DYNAMIC_ROLE_CONTRACT.md
|-- ROLE_DEFINITION_TEMPLATE.yaml
|-- ROLE_REGISTRY.yaml
|-- ROLE_ASSIGNMENT_CONTRACT.md
`-- ROLES/
    |-- ACTIVE/
    |   |-- GENERAL_SPECIALIST_ROLE.md
    |   `-- VALIDATION_ROLE.md
    `-- CANDIDATES/
        |-- RESEARCH_ROLE.md
        |-- TRANSLATION_ROLE.md
        |-- CONTENT_ROLE.md
        |-- BUSINESS_ANALYSIS_ROLE.md
        `-- DEVELOPMENT_ROLE.md
```

Related Runtime contracts are under `../docs/runtime-contracts/`. Program and Tool registration is under `../05_REGISTRIES/`.

## 3. Document Responsibilities

| Document | Responsibility |
| --- | --- |
| `THOMAS_PRIME_CHARTER.md` | Prime identity, responsibility, authority boundary, and prohibited behavior |
| `MVP_DYNAMIC_ROLE_CONTRACT.md` | Common contract inherited by every Dynamic Role |
| `ROLE_DEFINITION_TEMPLATE.yaml` | Machine-readable structure for one persistent Role |
| `ROLE_REGISTRY.yaml` | Current Role version, status, routability, hashes, and Candidate trial policy |
| `ROLE_ASSIGNMENT_CONTRACT.md` | Exact scope granted to one Role for one Task |
| `ROLES/ACTIVE/` | Roles available for normal Runtime routing |
| `ROLES/CANDIDATES/` | Roles available only for an explicitly approved isolated trial |

## 4. Definition, Registry, and Assignment

### Role Definition

Defines what a Role is capable of and its absolute limits.

- Persistent across Tasks.
- Holds capabilities, unsupported capabilities, authority ceiling, resource allowlists, Memory policy, and output requirements.
- Does not grant Runtime authority by itself.

### Role Registry

Defines which exact Role version the Runtime can discover and whether it can be routed.

- Source of truth for status, version, routability, contract path, and integrity hashes.
- Does not replace the Role Definition.
- Does not grant Task-specific scope.

### Role Assignment

Defines what one selected Role may do for one exact Task.

- Pins the exact Role ID and version.
- Grants only the required capability subset, authority, Core rules, Memory scope, Program and Tool scope, validation requirement, and numeric budget.
- Cannot exceed the Role Definition or System Policy.

```text
MVP Dynamic Role Contract
-> Role Definition
-> Role Registry
-> Role Assignment
-> Agent Output
```

## 5. Role Status

### `active`

- Normal routing is allowed only when `routable: true`.
- Every Registry, capability, authority, Permission Decision, resource, validation, and budget check must still pass.

### `candidate`

- Normal routing is prohibited and `routable` must be `false`.
- An explicit, isolated Candidate trial may be assigned under Registry trial rules.
- A successful trial does not activate the Role.

### `disabled`

- All new Assignments are prohibited.
- Existing history and references remain readable for Audit.
- Reactivation requires Thomas approval, contract review, version review, and Registry update.

`draft`, `deprecated`, and `archived` Roles are also unavailable for normal routing.

## 6. Candidate to Active Promotion

```text
Repeated need
-> Candidate Definition
-> Exact-version isolated trial
-> Independent validation
-> Quality, cost, latency, failure, authority, and safety comparison
-> Thomas approval
-> Role version and Registry update
-> active + routable
```

Minimum requirements:

1. At least three similar completed Tasks demonstrate repeated need.
2. The Role provides measurable value beyond General Specialist.
3. Candidate trial, validation, authority, budget, and safety checks pass.
4. Thomas approves the exact Role version.
5. Registry and Audit are updated.

No Agent, Role, or Registry process may promote a Candidate automatically.

## 7. Role Selection Flow

```text
Task v0.3
-> Can a registered Program complete it deterministically?
   -> YES: Program path
   -> NO: Role path
-> active + routable Registry lookup
-> Definition hash and capability-set verification
-> Minimum sufficient capability match
-> Authority ceiling check
-> Task routing Permission Decision check
-> Numeric budget check
-> Resource and validation check
-> Role Assignment v0.2
-> Role execution
-> Agent Output v0.2
-> Validation when required
-> Prime integration
```

Candidate Roles are excluded from this normal flow.

## 8. Authority and Permission

Authority Level and Permission Decision are separate.

```text
Authority Level: P0 through P6
Permission Decision: ALLOW | EXECUTE_AND_REPORT | APPROVAL_REQUIRED | BLOCK
```

Effective authority is the minimum of all applicable ceilings:

```text
required_authority_level
<= effective_authority_level
<= assignment_granted_authority_level
<= role_authority_ceiling
<= system_actor_authority_ceiling
```

A sufficient Authority Level does not authorize the exact action. The action must also have a valid Permission Decision. `APPROVAL_REQUIRED` never increases authority.

P5 and P6 always pass through separate Policy Gates. Tool, Program, Subtask, and delegated Role use cannot expand authority.

The canonical rules are in `../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md`.

## 9. Document Precedence

```text
Active Thomas Core
-> Active Operating Constitution (skip while inactive)
-> MVP Operating Policy
-> Runtime Contract Precedence Addendum
-> Canonical Runtime Contracts
-> Thomas Prime Charter
-> MVP Dynamic Role Contract
-> Role Definition
-> Role Assignment
-> Runtime Defaults
```

Lower documents may narrow an upper rule but cannot expand authority, Permission, resource scope, Memory scope, or budget beyond it. An unresolved conflict blocks execution and is escalated to Thomas Prime.

## 10. Runtime Read Order

### Startup

```text
CORE_METADATA.yaml
-> MVP_ACTIVE_CORE.yaml
-> MVP Operating Policy
-> Runtime Contract Precedence Addendum
-> Canonical Runtime Contracts
-> Thomas Prime Charter
-> MVP Dynamic Role Contract
-> ROLE_REGISTRY.yaml
```

### Task Routing

```text
Task v0.3
-> Registry lookup
-> Exact Role Definition version
-> Definition and capability integrity checks
-> Authority, Permission, budget, resource, and validation checks
-> Role Assignment v0.2
```

### Role Execution

```text
Role Assignment v0.2
-> Exact Role Definition version
-> Assigned inputs, Core rules, Memory, resources, and budget
-> Agent Output v0.2
```

## 11. Version and Change Policy

- Role Definitions use Semantic Versioning.
- Registry and Role Assignment pin one exact Role version.
- A Definition change does not alter a running Assignment.
- Capability, authority ceiling, status, routability, resource allowlist, or integrity hash changes require Registry update and Audit.
- Agents and Roles cannot modify their own Definition, status, authority, Permission, resource allowlist, budget cap, or Registry record.

## 12. Runtime Summary

```text
System Policy
>= System Actor Authority Ceiling
>= Role Authority Ceiling
>= Assignment Granted Authority
>= Effective Runtime Authority

AND

Exact Action Permission Decision
= ALLOW or EXECUTE_AND_REPORT
```

Both conditions must be satisfied before execution.
