# PR #8 — Active Registry Direct Slimming

**Status:** Implementation package
**Baseline:** I0.5.5 Architecture Slimming sequence after PR #7
**Runtime authority expansion:** None
**Tool or Program enablement:** None

## Decision

The active Role, Program, and Tool Registries are slimmed in place. The former parallel `*_SLIM_CANDIDATE.yaml` files are no longer alternative Registry sources and become temporary non-runtime migration references.

```text
Role capability / restrictions / permission ceiling / validation
→ Role Definition

Role status / routability / Definition path / Definition hash
→ Active Role Registry

Program behavior / permission / determinism / effects
→ Program Definition

Program status / enablement / Definition path / Definition hash / implementation availability
→ Active Program Registry

Tool behavior / permission / effects
→ Tool Definition

Tool status / enablement / class / Definition path / Definition hash / implementation availability
→ Active Tool Registry
```

## Compatibility boundary

Legacy consumers may receive an in-memory projection built from:

```text
Active slim Registry
+ hash-verified canonical Definition
+ Governance reference
→ non-authoritative legacy view
```

The projection:

- is never persisted;
- is never a Source of Truth;
- cannot activate a Role, Program, or Tool;
- cannot expand Permission or Authority;
- fails closed on a missing Definition, hash mismatch, identity mismatch, status mismatch, or Runtime-state mismatch.

## Preserved safety state

```yaml
runtime_authoritative_execution: false
program_execution_enabled: false
tool_execution_enabled: false
program_runtime_implementation_available: false
tool_runtime_implementation_available: false
external_action_enabled: false
financial_action_enabled: false
permission_expansion_enabled: false
authority_expansion_enabled: false
```

## Out of scope

PR #8 does not make `governance/GOVERNANCE_POLICY.yaml` authoritative. Governance atomic consolidation remains PR #9.
