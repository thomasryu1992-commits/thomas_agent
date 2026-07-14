# PR #9 — Governance Atomic Consolidation

- **Status:** `IMPLEMENTATION_PACKET`
- **Baseline:** PR #8 Active Registry Direct Slimming
- **Runtime behavior change:** None
- **Runtime authority expansion:** None
- **External or financial effect:** None

## Objective

Promote one machine-readable Governance Policy to active authority and remove duplicated rule ownership from Permission/Approval policy files, human-readable principles, Authority explanations, Action Fingerprint documentation, MVP policy prose, Runtime adapters, and Validators.

## Canonical ownership

```text
Authority levels and invariants
Effect classification
Permission dispositions
Approval requirements and TTL
Action identity and fingerprint rules
Control Channel requirements
GitHub and financial boundaries
Role / Tool / Program governance
Memory / learning governance
Validation governance
Kill Switch governance
Conflict resolution
        ↓
governance/GOVERNANCE_POLICY.yaml
```

The canonical policy is authoritative for policy decisions only. It does not activate Runtime or an Executor.

## Record boundaries retained

```text
PermissionDecision
→ immutable action-specific policy-result record

Approval
→ immutable action-bound Thomas-decision and lifecycle record
```

These records remain separate because they have independent lifecycle, lineage, schema, and Audit responsibilities. They reference the canonical policy and do not redefine it.

## Compatibility

Historical records bound to:

```text
thomas.permission_approval.operating_policy
v0.1.0
docs/runtime-contracts/THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml
```

remain interpretable under their historical schema. New records bind:

```text
thomas.governance.policy
v1.1.0
governance/GOVERNANCE_POLICY.yaml
```

The Permission and Approval schemas accept both bindings for historical compatibility. The active semantic Validator requires the canonical binding for new current examples and generated records.

## Superseded rule owners

The following artifacts become explanatory or compatibility references:

- `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml`;
- `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md`;
- `AUTHORITY_AND_PERMISSION_MODEL.md`;
- `ACTION_FINGERPRINT_POLICY_V0.1.md`;
- Governance sections inside `MVP_OPERATING_POLICY.md`;
- Governance rows and binding text in the Runtime Contract Precedence Addendum.

They may explain history, record structure, or algorithms. They may not own current policy rules.

## Atomic cutover requirements

The following changes must land together:

1. canonical policy promotion;
2. policy identity and source-path cutover;
3. Permission/Approval schema compatibility update;
4. current Example and Fixture binding migration;
5. Permission/Approval Validator cutover;
6. read-only Kernel policy-adapter cutover;
7. Slimming Validator and focused test update;
8. documentation authority demotion;
9. active architecture and document index update.

Partial application is prohibited because it could create two active policy identities or leave current records bound to a superseded source.

## Safety invariants

PR #9 must preserve:

```yaml
runtime_effect:
  mode: REVIEW_ONLY
  grants_runtime_execution: false
  grants_tool_or_program_enablement: false
  grants_external_execution: false
  grants_financial_execution: false
  grants_permission_expansion: false
  executor_handoff_allowed: false
  approval_consumption_allowed: false
  core_activation_allowed: false
```

Additional invariants:

- Thomas remains the final human authority.
- Approval cannot expand Authority.
- Validation cannot grant Permission, Approval, Authority, activation, or execution.
- Missing or inconsistent Governance data fails closed.
- Historical records are not silently rewritten.
- New current records use the canonical policy binding.
- Policy authority does not imply Runtime authority.

## Validation

Focused validation must cover:

- one active machine-readable policy source;
- no duplicate Permission scope ownership;
- canonical policy identity and version;
- complete Authority P0–P6 map;
- exact action-fingerprint policy;
- Control Channel and TTL rules;
- Runtime-effect guards all false;
- historical schema binding accepted;
- current record binding required;
- positive Permission/Approval examples;
- existing negative fixtures;
- active Slimming tests;
- Active Architecture Gate;
- Full Repository Gate before release.

## Non-goals

PR #9 does not:

- activate Runtime-authoritative entry;
- consume Approval;
- activate an Executor;
- enable Tool or Program execution;
- enable model or network access;
- enable filesystem writes;
- enable external or financial effects;
- activate Core;
- compress Deferred architecture;
- delete historical evidence.
