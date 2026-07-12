# Authority and Permission Model

**Document Version:** `0.1.0`
**Status:** `Active MVP Contract`
**Owner:** `Thomas`

## 1. Purpose

This document separates two concepts that must never be treated as the same value.

1. **Authority Level** — what an actor is structurally allowed to do.
2. **Permission Decision** — whether one exact requested action may proceed now.

The model prevents invalid comparisons such as `ALLOW <= P3` and keeps Role, Task, Assignment, Tool, Program, Policy, Approval, and Runtime decisions auditable.

## 2. Authority Levels

```text
P0 Observe
P1 Read
P2 Analyze
P3 Create
P4 Internal Modify
P5 External Action
P6 Critical Authority
```

### P0 — Observe

- Read status indicators.
- Read non-content operational metadata.

### P1 — Read

- Read authorized documents, data, Memory, and Task context.

### P2 — Analyze

- Compare, calculate, evaluate, classify, and reason over authorized inputs.

### P3 — Create

- Create drafts, plans, analyses, reports, internal artifacts, and Memory Candidates.

### P4 — Internal Modify

- Modify explicitly assigned and reversible internal state.
- Modify Working Memory or Task state only when separately allowed.

### P5 — External Action

- Send messages, publish content, or modify external systems.
- Requires a separate Policy Gate and action-bound approval when policy requires it.

### P6 — Critical Authority

- Financial action, critical permission change, Core change, Policy change, high-impact deployment, destructive privileged action, or equivalent critical authority.
- Thomas-only or separately governed by an explicit approved policy.

## 3. Canonical Authority Fields

```yaml
required_permission_level: P2
role_permission_ceiling: P3
assignment_granted_permission_level: P2
effective_permission_level: P2
```

- `required_permission_level`: minimum authority required by the planned action.
- `role_permission_ceiling`: maximum authority the selected Role Definition may ever receive.
- `assignment_granted_permission_level`: maximum authority Thomas Prime grants for this exact Assignment.
- `effective_permission_level`: final authority available after actor, Role, Assignment, resource, and policy constraints.

## 4. Permission Decisions

Permission Decisions are not authority levels.

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

### `ALLOW`

The exact action may proceed within the effective authority, scope, resource, and budget constraints.

### `EXECUTE_AND_REPORT`

The exact action may proceed and must be reported afterward.

### `APPROVAL_REQUIRED`

The action must not execute until a valid action-bound approval is issued and consumed.

### `BLOCK`

The action must not execute.

## 5. Canonical Evaluation Order

Authority is calculated first.

```text
effective_permission_level =
minimum_authority(
  system_actor_scope,
  role_permission_ceiling,
  assignment_granted_permission_level,
  tool_or_program_authority_scope
)
```

Then Runtime checks:

```text
required_permission_level <= effective_permission_level
```

If false, the action is blocked and a new Permission Decision or Role Assignment is required.

After authority sufficiency is confirmed, the Policy Engine decides whether the exact action may proceed.

```text
Authority Sufficient?
  NO  -> BLOCK
  YES -> Policy Decision
            ALLOW
            EXECUTE_AND_REPORT
            APPROVAL_REQUIRED
            BLOCK
```

## 6. Invariants

```text
Actual Runtime Authority
<= Assignment Granted Permission Level
<= Role Permission Ceiling
<= System Actor Maximum
```

The following rules always apply.

- Permission Decision values are never compared numerically with P0–P6.
- A Role Assignment cannot grant authority above the Role Permission Ceiling.
- A Tool or Program cannot expand an actor's authority.
- A Policy Decision may narrow, require approval for, or block an otherwise authorized action.
- `APPROVAL_REQUIRED` does not increase authority by itself.
- Approval is valid only for the exact approved action fingerprint, target, content, amount, Tool, scope, and expiration.
- New authority requires a new Permission Decision and, when Assignment scope changes, a new Role Assignment.
- P5 and P6 always require separate policy gates.

## 7. Task Contract Use

```yaml
authority:
  required_permission_level: P2

permission:
  permission_decision: ALLOW
  permission_decision_ref: perm_01HX_example
```

The Task Permission Decision does not replace the Role Assignment authority fields.

## 8. Role Assignment Use

```yaml
authority:
  required_permission_level: P2
  role_permission_ceiling: P3
  assignment_granted_permission_level: P2
  effective_permission_level: P2

permission:
  permission_decision: ALLOW
  permission_decision_ref: perm_01HX_example
```

## 9. External Action Use

A Specialist may analyze or draft content for a future external action while remaining at P2 or P3.

The actual external action is a separate P5 Execution Request handled by the Restricted Execution Service.

```text
Specialist Role
P2/P3
-> analysis or draft

Validation
-> review

Policy Engine
-> APPROVAL_REQUIRED

Thomas Approval
-> action-bound approval

Restricted Execution Service
P5-scoped execution
```

A Task that includes a future external action does not automatically disqualify an internal drafting or analysis Role.

## 10. Final Rule

> Authority answers: “Can this actor class perform this action type within this scope?”

> Permission answers: “May this exact action proceed now?”

> Runtime must satisfy both questions independently before execution.
