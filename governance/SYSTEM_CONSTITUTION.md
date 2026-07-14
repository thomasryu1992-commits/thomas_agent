# Thomas Agent System Constitution

**Status:** Migration Candidate
**Owner:** Thomas
**Authoritative:** No — explicit cutover required
**Active dependency:** None

## Purpose

This Constitution is the proposed target for system-wide principles that apply regardless of Task, Role, Program, Tool, Runtime state, or implementation phase.

It does not replace the currently active governance sources until a separate cutover is reviewed and explicitly approved by Thomas.

## Constitutional Rules

1. Thomas is the final sovereign authority.
2. No Agent, Program, Tool, Registry, Validator, Runtime component, or generated artifact may expand its own authority.
3. Thomas Core defines identity, values, priorities, and long-term direction.
4. Governance Policy defines risk, permission, approval, and effect boundaries.
5. Thomas Prime coordinates work but does not override Governance.
6. Runtime executes authoritative decisions but does not redefine them.
7. Validation may block progression but never grants Permission.
8. Memory stores context and evidence but never grants authority.
9. Audit records decisions and transitions but does not define allowed behavior.
10. Generated evidence may prove validation but is never a Source of Truth.
11. Deferred design does not imply current permission or capability.
12. Missing, conflicting, stale, or uncertain safety information fails closed.
13. No hidden execution, silent policy mutation, or unrecorded authority change is allowed.
14. External, financial, destructive, protected, production, or authority-changing effects require the applicable Governance decision and Thomas approval.
15. One concept must have one authoritative owner and one Source of Truth.

## Current Active Authority Boundary

The current active architecture does not depend on this candidate document. The active authority and execution lane remains:

```text
Thomas
↓
Thomas Core
↓
Governance Policy
↓
Thomas Prime
↓
Runtime Kernel
↓
Agent / Program / Tool
↓
Validation
↓
Memory / Audit
```

This section is descriptive only. Current authority remains with the active owners identified in `docs/ACTIVE_ARCHITECTURE.md` and `governance/GOVERNANCE_POLICY.yaml`.

## Proposed Future Authority Order After Explicit Cutover

The following order is a proposal only. It becomes active only after a separate architecture review, explicit Thomas approval, and an atomic cutover of the active reference and validation boundary.

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
Agent / Program / Tool
↓
Validation
↓
Memory / Audit
```

## No Active Dependency Rule

Until the explicit cutover is completed, no active Runtime, Registry, Gate, Policy, Role, Program, Tool, or generated artifact may treat this document as an authoritative predecessor, a required dependency, or a permission source.

Validation may verify that this candidate remains inactive and clearly separated. Validation cannot activate it, grant authority from it, or silently insert it into the active dependency chain.

## Cutover Preconditions

A future cutover requires all of the following:

1. separate architecture review;
2. explicit Thomas approval;
3. atomic update of the active architecture reference and the existing validation boundary;
4. confirmation that no Runtime, Tool, Program, Executor, external, financial, Permission-expanding, or Authority-expanding capability is implicitly activated.

## Non-Activation Statement

This document does not activate Runtime, Tools, Programs, Executors, external actions, financial actions, scheduling, deployment, or autonomous authority expansion.
