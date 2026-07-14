# Authority and Permission Model — Explanatory Reference

**Document Version:** `0.2.0`
**Status:** `EXPLANATORY_REFERENCE`
**Owner:** `Thomas`
**Authoritative:** `false`
**Canonical Governance Source:** [`governance/GOVERNANCE_POLICY.yaml`](../../governance/GOVERNANCE_POLICY.yaml)

## 1. Purpose

This document explains the distinction between Authority and Permission. The canonical Authority levels, invariants, Permission dispositions, Approval requirements, effect classifications, and conflict rules are defined only in `governance/GOVERNANCE_POLICY.yaml`.

```text
Authority
→ What an actor class may structurally do within its maximum scope

Permission
→ Whether one exact action may proceed now
```

Authority and Permission must never be treated as the same axis. Values such as `ALLOW` and `BLOCK` are not numerically comparable with `P0` through `P6`.

## 2. Authority levels

| Level | Meaning | Typical boundary |
|---|---|---|
| `P0` | Observe | Status and non-content metadata |
| `P1` | Read | Assigned documents, data, Memory, and Task context |
| `P2` | Analyze | Compare, calculate, evaluate, classify, and reason |
| `P3` | Create | Drafts, plans, analyses, reports, internal artifacts, and candidates |
| `P4` | Internal Modify | Explicitly assigned, reversible internal state |
| `P5` | External Action | Messages, publication, or external-system state change |
| `P6` | Critical Authority | Financial, Core, policy, privileged, destructive, or equivalent critical action |

This table is explanatory. The machine-readable level map is owned by the canonical Governance Policy.

## 3. Canonical Authority invariant

```text
required_permission_level
<= effective_permission_level
<= assignment_granted_permission_level
<= role_permission_ceiling
<= system_actor_maximum
```

If the chain is incomplete, unknown, or insufficient, the result is `BLOCK`.

Approval cannot expand Authority. A Tool or Program cannot expand Authority. A Role cannot change its own ceiling. Runtime cannot activate itself.

## 4. Permission dispositions

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

- `ALLOW`: the exact action may proceed only within valid Authority, scope, lineage, resource, budget, and Runtime boundaries.
- `EXECUTE_AND_REPORT`: the exact action may proceed only when the canonical policy's reversibility, scope, versioning, rollback, and reporting requirements are satisfied.
- `APPROVAL_REQUIRED`: the action must not proceed until a valid exact-action Approval exists. Current Review-only records still do not create an execution token.
- `BLOCK`: the action must not proceed.

The scope-to-disposition map is defined only in `governance/GOVERNANCE_POLICY.yaml`.

## 5. Evaluation sequence

```text
Task / requested action
        ↓
Authority lineage and ceiling check
        ↓
Exact action fingerprint
        ↓
Canonical Governance Policy evaluation
        ↓
ALLOW / EXECUTE_AND_REPORT / APPROVAL_REQUIRED / BLOCK
        ↓
Runtime boundary check
```

A sufficient Authority level is necessary but not sufficient. Policy may narrow, require Approval, or block an otherwise authorized action.

## 6. Record responsibilities

| Record | Responsibility | Does not own |
|---|---|---|
| `PermissionDecision` | Immutable result and evidence for one exact action | Global Governance rules |
| `Approval` | Thomas decision and lifecycle evidence for one action-bound Permission Decision | Authority expansion or execution permission |
| `RoleAssignment` | Task-specific Authority grant and scope | Global Permission policy |
| `Task` | Required level and decision references | Policy rule definitions |

Records preserve decisions and lineage. They do not redefine the rules that produced those decisions.

## 7. External action example

```text
Specialist Role at P2/P3
→ internal analysis or draft

Independent Validation when required
→ review only

Canonical Governance Policy
→ APPROVAL_REQUIRED for the external effect

Action Approval
→ exact-action evidence only

Future separately approved Executor
→ still required for any real external action
```

A Task that may later produce an external action can still contain internal analysis or drafting. The external effect is a separate action with its own Authority, Permission, Approval, hot-path risk, and Executor requirements.

## 8. Final rule

> Authority answers: “Can this actor class perform this action type within this scope?”
>
> Permission answers: “May this exact action proceed now?”
>
> Runtime must satisfy both questions independently and must still preserve every no-effect and execution-stage boundary.
