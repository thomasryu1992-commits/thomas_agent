# MVP Dynamic Role Contract

**Document Version:** `0.3.0`  
**Document Status:** `Reviewed MVP Contract`  
**Owner:** `Thomas`  
**Applies To:** Dynamic Specialist, Independent Validator, Risk Reviewer

**Authority Model:** [`../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md`](../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md)  
**Agent Output:** [`agent_output.v0.2`](../docs/runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md)  
**Execution Budget:** [`execution_budget.v0.1`](../docs/runtime-contracts/EXECUTION_BUDGET_SCHEMA.yaml)

## 1. Purpose

This document defines the common Runtime contract for every Dynamic Role.

A Role Definition describes persistent capability and absolute limits.

A Role Assignment describes the exact Task-specific objective, capability subset, authority, resources, Memory scope, validation, and budget.

A Role cannot execute without both a valid Role Definition and a valid Role Assignment.

Thomas Prime is not a Dynamic Role and does not inherit this contract.

## 2. Contract Layers

```text
MVP Dynamic Role Contract
→ common rules

Role Definition
→ persistent purpose, capability, and ceilings

Role Registry
→ current status, version, routability, and lookup metadata

Role Assignment
→ exact Task-specific scope and granted limits

Agent Output v0.2
→ traceable execution result
```

## 3. Document Precedence

```text
Active Thomas Core
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
Role Definition
↓
Role Assignment
↓
Runtime Defaults
```

Inactive or reference-only documents do not create Runtime permission.

Unresolvable conflicts must stop execution and be escalated to Thomas Prime.

## 4. Required Role Definition Fields

Every Role Definition must contain:

```text
schema_version
role_id
role_name
role_version
status
routable
role_type
purpose
capabilities
unsupported_capabilities
activation_conditions
non_activation_conditions
deactivation_conditions
input_contract
active_core
authority_ceiling
allowed_program_ids
allowed_tool_ids
memory_policy
output_contract
validation_policy
budget_caps
stop_conditions
completion_criteria
quality_criteria
escalation
change_control
```

Allowed Dynamic Role types:

```text
dynamic_specialist
independent_validator
risk_reviewer
```

## 5. Role Status

```text
draft
candidate
active
disabled
deprecated
archived
```

Normal Runtime selection requires:

```text
status: active
routable: true
```

Candidate Roles are never available for normal automatic routing.

Candidate trial Assignments may be issued only under the explicit Candidate Trial Policy.

## 6. Input Contract

Common inputs are:

```text
Task v0.3
+
Role Assignment v0.2
```

Role Definitions do not redefine Task fields.

A Role may use only the `input_refs`, `context_refs`, Active Core Rule IDs, Memory scope, Program IDs, and Tool IDs included in the Assignment.

Missing inputs:

- Low impact: use a safe assumption and disclose it.
- Material impact: stop and request input from Prime.
- High risk: stop and escalate to Prime.

## 7. Active Core Scope

A Role does not load the full Core by default.

Prime assigns only relevant `active_core_rule_ids`.

Reference-only Core requires an explicit Assignment allowlist or Context Reference.

Inactive Core Candidates are prohibited.

A Role may apply assigned Core rules but cannot modify, expand, activate, or reinterpret them into new authority.

## 8. Authority and Permission

Authority Level and Permission Decision are separate axes.

Canonical authority fields:

```yaml
required_authority_level: P2
role_authority_ceiling: P3
assignment_granted_authority_level: P2
effective_authority_level: P2
```

Canonical Permission Decisions:

```text
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK
```

A Role may execute only when:

```text
required_authority_level
<= effective_authority_level
<= assignment_granted_authority_level
<= role_authority_ceiling
```

and the exact action has a valid Permission Decision.

A Tool, Program, Subtask, or delegated Role cannot expand authority.

## 9. Capability, Program, and Tool

A Role performs only capabilities registered in its Definition and assigned in the Role Assignment.

Program and Tool use requires all of the following:

- Registered in the appropriate Registry.
- Active and enabled.
- Allowed by the Role Definition.
- Allowed by the Role Assignment.
- Within effective authority.
- Within numeric execution budget.
- Allowed by the current Permission Decision.

Unregistered or disabled resources are blocked.

## 10. Memory

A Role reads only Memory references and scopes in the Assignment.

Default rules:

- Related Task Working Memory and related Validated Memory may be read only when assigned.
- Unrelated private Memory and inactive Core Candidates are prohibited.
- Direct Validated Memory write is prohibited.
- Direct Core write is prohibited.
- Reusable information may be returned only as a Memory Candidate.
- Secrets, API keys, tokens, passwords, recovery codes, and private keys must not become Memory Candidates.

## 11. Agent Output

Every Dynamic Role returns `agent_output.v0.2`.

Required lineage:

```text
Task ID
+
Assignment ID
+
Actor Instance ID
+
Role ID
+
Role Version
```

Role-specific fields must be placed under:

```yaml
role_specific_output: {}
```

Common fields and statuses must not be redefined by individual Roles.

## 12. Validation

Effective validation is the highest requirement from:

```text
Operating Policy
Task
Role Definition
Role Assignment
```

Independent Validation requires a different Agent instance or fresh execution context.

The creator cannot treat self-review as independent validation.

Validation results:

```text
PASS
REVISE
BLOCK
```

Validation does not grant execution permission.

## 13. Execution Budget

All new Assignments use `execution_budget.v0.1`.

Effective budget is calculated per field:

```text
minimum(
  Operating Policy limit,
  Parent Task remaining budget,
  Task allocation,
  Role Definition cap when not null,
  Role Assignment allocation
)
```

Task and Assignment numeric limits are required.

A null Role cap means only that the Role adds no additional cap for that field. It never means unlimited Runtime budget.

Subtasks and new Assignments cannot increase the parent remaining budget.

## 14. Candidate Trial Policy

Candidate Roles remain:

```yaml
status: candidate
routable: false
```

A Candidate trial is allowed only when all requirements are met:

```text
Thomas approval
Exact Candidate Role Version
Explicit candidate_trial Assignment
Isolated trial context
No external action
No persistent Runtime change
Numeric execution budget
Independent validation
Full audit record
```

Candidate trial permission does not activate or promote the Role.

Promotion requires separate Thomas approval and a Registry version update.

## 15. Stop, Failure, and Escalation

Immediate stop conditions include:

- Permission ceiling exceeded.
- Assignment scope exceeded.
- Prohibited or unregistered Tool or Program required.
- Security or secret-exposure risk.
- Critical input corruption.
- Budget exhausted.
- Policy or Active Core conflict.
- Approval scope mismatch.

Automatic retry is prohibited for:

- Permission error.
- Security error.
- Data corruption risk.
- High-risk external action failure.
- Approval scope violation.

The Dynamic Role escalates to Thomas Prime, not directly to Thomas.

## 16. Completion and Quality

Completion and quality are separate.

Completion checks required output presence and declared status.

Quality checks objective alignment, evidence quality, logic, uncertainty disclosure, Core alignment, contract validity, authority compliance, and budget compliance.

Partial output must disclose:

- Completed scope.
- Missing scope.
- Impact.
- Next action.

## 17. Change Control

Role Definitions use Semantic Versioning.

Agents and Roles cannot directly modify:

- Their own Definition.
- Role status.
- Permission ceiling.
- Tool or Program allowlist.
- Budget cap.
- Registry record.

They may create a versioned change proposal.

Role activation, deactivation, permission-ceiling changes, common routing changes, and Candidate promotion require Thomas approval and Audit.
