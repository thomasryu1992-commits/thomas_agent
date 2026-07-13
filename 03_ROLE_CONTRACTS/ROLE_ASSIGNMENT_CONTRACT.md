# Role Assignment Contract

**Schema Version:** `role_assignment.v0.2`
**Document Version:** `0.2.2`
**Status:** `MVP Reviewed Contract`
**Owner:** `Thomas`

**Role Standard:** [`MVP_DYNAMIC_ROLE_CONTRACT.md`](./MVP_DYNAMIC_ROLE_CONTRACT.md)
**Authority Model:** [`AUTHORITY_AND_PERMISSION_MODEL.md`](../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md)
**Agent Output:** [`agent_output.v0.2`](../docs/runtime-contracts/AGENT_OUTPUT_CONTRACT_V0.2.md)
**Execution Budget:** [`execution_budget.v0.1`](../docs/runtime-contracts/EXECUTION_BUDGET_SCHEMA.yaml)

## 1. Purpose

Role Assignment records the exact `task.v0.3` revision, Core Binding, and Task-specific scope granted to one Role.

Role Definition alone never creates Runtime authority.

```text
Task
↓
Thomas Prime selects a Role
↓
Registry, capability, authority, permission, resource, and budget checks
↓
Role Assignment v0.2
↓
Role execution
↓
Agent Output v0.2
```

## 2. Required Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | `role_assignment.v0.2` |
| `assignment_id` | string | Unique Assignment ID |
| `assignment_mode` | string | `normal` or `candidate_trial` |
| `trace_id` | string | End-to-end Trace ID |
| `task_id` | string | Related Task |
| `core_context_binding_id` | string | Exact Core Context Binding inherited from the Task revision |
| `parent_task_id` | string or null | Parent Task when applicable |
| `role_id` | string | Exact Registry Role ID |
| `role_version` | string | Exact Role Definition version |
| `role_definition_ref` | string | Registry or file reference |
| `actor_instance_id` | string | Runtime Agent instance |
| `assigned_by` | string | Usually `thomas_prime` |
| `assignment_status` | string | Assignment state |
| `role_scope` | object | Exact objective, capability subset, outputs, completion, and quality |
| `input_refs` | array | Inputs allowed for this Assignment |
| `context_refs` | array | Documents, prior outputs, and non-Memory context |
| `active_core_rule_ids` | array | Active Core rules assigned |
| `memory_scope` | object | Read and Candidate-creation scope |
| `authority` | object | Required, ceiling, granted, and effective P-levels |
| `permission` | object | Exact Permission Decision and references |
| `allowed_program_ids` | array | Effective Program allowlist |
| `allowed_tool_ids` | array | Effective Tool allowlist |
| `validation` | object | Effective validation requirement |
| `execution_budget` | object | `execution_budget.v0.1` |
| `constraints` | array | Hard limits and prohibited behavior |
| `escalation_target` | string | `thomas_prime` |
| `trial_authorization_ref` | string or null | Required for Candidate trial |
| `expires_at` | string | Assignment expiration |
| `created_at` | string | UTC timestamp |


## Core Binding Lineage

Every Role Assignment must reference the same `core_context_binding_id` as its Task revision.

The Assignment cannot select, upgrade, or reinterpret a Core Release independently.

```text
Task.core_context_binding_id
=
RoleAssignment.core_context_binding_id
```

If a material Core rebind occurs, the Task revision changes and affected Assignments are invalidated and reissued.

The Binding identifies the governing Core. It does not grant Permission.


## 3. Assignment Status

```text
ASSIGNED
RUNNING
OUTPUT_READY
VALIDATING
COMPLETED
BLOCKED
FAILED
CANCELED
EXPIRED
```

Assignment state is separate from Task state and Agent Output status.

## 4. Role Scope

Every Assignment must define the exact portion of the Task owned by the Role.

```yaml
role_scope:
  role_objective: ""
  assigned_capabilities: []
  excluded_capabilities: []
  required_outputs: []
  completion_criteria: []
  quality_criteria: []
```

The Role cannot use capabilities that are not both:

1. Present in the Role Definition.
2. Present in `assigned_capabilities`.

## 5. Authority and Permission

```yaml
authority:
  required_permission_level: P2
  role_permission_ceiling: P3
  assignment_granted_permission_level: P2
  effective_permission_level: P2

permission:
  permission_decision: ALLOW
  permission_decision_ref: perm_01HX_example
  approval_id: null
```

Required invariant:

```text
required_permission_level
<= effective_permission_level
<= assignment_granted_permission_level
<= role_permission_ceiling
```

Permission Decision is evaluated separately.

`APPROVAL_REQUIRED` does not grant a higher P-level.

## 6. Memory Scope

```yaml
memory_scope:
  readable_memory_refs: []
  readable_scopes: []
  prohibited_scopes:
    - unrelated_private_memory
    - inactive_core_candidates
    - restricted_memory
  memory_candidate_creation_allowed: true
  allowed_candidate_types: []
  validated_memory_write_allowed: false
  core_memory_write_allowed: false
```

`context_refs` do not implicitly grant access to every Memory scope.

## 7. Program and Tool Scope

A resource must be:

- Active in Registry.
- Allowed by Role Definition.
- Allowed by Assignment.
- Within effective authority.
- Within budget.
- Allowed by Permission Decision.

An empty Assignment allowlist means no resource of that class may be used.

## 8. Validation

```yaml
validation:
  mode: automatic
  validator_role_id: null
  acceptance_criteria: []
  rejection_criteria: []
  maximum_cycles: 1
```

Allowed modes:

```text
automatic
independent
risk_review
```

The Assignment may increase but never lower a Policy or Task validation requirement.

## 9. Execution Budget

`execution_budget` must use `execution_budget.v0.1`.

All Task and Assignment limits must be numeric.

The Role cap is an upper bound only.

Subtasks and new Assignments cannot increase the parent remaining budget.

## 10. Candidate Trial

Candidate Roles cannot receive a normal Assignment.

A Candidate trial requires:

```yaml
assignment_mode: candidate_trial
trial_authorization_ref: approval_candidate_trial_01HX
```

and all Candidate Trial Policy requirements in the Registry.

A trial never changes:

```text
status: candidate
routable: false
```

## 11. Valid Example

```yaml
schema_version: role_assignment.v0.2
assignment_id: assignment_01HX_example
assignment_mode: normal
trace_id: trace_01HX_example
task_id: task_01HX_example
core_context_binding_id: ccb-assignment-example-001
parent_task_id: null

role_id: general.specialist
role_version: 0.3.0
role_definition_ref: role_registry:general.specialist@0.3.0
actor_instance_id: agent_instance_01HX_example
assigned_by: thomas_prime
assignment_status: ASSIGNED

role_scope:
  role_objective: Analyze the supplied material and identify material risks.
  assigned_capabilities:
    - analysis
  excluded_capabilities:
    - external_execution
  required_outputs:
    - structured_analysis
    - risk_summary
  completion_criteria:
    - requested_output_present
    - limitations_disclosed
  quality_criteria:
    - evidence_and_inference_separated
    - uncertainty_disclosed

input_refs:
  - task.request.raw_request
context_refs: []

active_core_rule_ids:
  - MVP_RULE_006
  - MVP_RULE_008

memory_scope:
  readable_memory_refs: []
  readable_scopes:
    - task_working_memory
  prohibited_scopes:
    - unrelated_private_memory
    - inactive_core_candidates
    - restricted_memory
  memory_candidate_creation_allowed: true
  allowed_candidate_types:
    - reusable_knowledge
    - project_learning
  validated_memory_write_allowed: false
  core_memory_write_allowed: false

authority:
  required_permission_level: P2
  role_permission_ceiling: P3
  assignment_granted_permission_level: P2
  effective_permission_level: P2

permission:
  permission_decision: ALLOW
  permission_decision_ref: perm_01HX_example
  approval_id: null

allowed_program_ids: []
allowed_tool_ids: []

validation:
  mode: automatic
  validator_role_id: null
  acceptance_criteria:
    - objective_met
    - output_contract_valid
    - no_authority_violation
  rejection_criteria:
    - material_evidence_misrepresentation
  maximum_cycles: 1

execution_budget:
  schema_version: execution_budget.v0.1
  limits:
    max_agent_invocations: 1
    max_model_calls: 3
    max_tool_calls: 0
    max_program_calls: 0
    max_revision_cycles: 1
    max_validation_cycles: 1
    max_retry_count: 1
    max_parallel_workers: 1
    max_runtime_seconds: 600
    token_budget: 20000
    cost_budget: 1.0
    cost_currency: USD
  usage:
    agent_invocations: 0
    model_calls: 0
    tool_calls: 0
    program_calls: 0
    revision_cycles: 0
    validation_cycles: 0
    retry_count: 0
    peak_parallel_workers: 0
    runtime_seconds: 0
    tokens_used: 0
    cost_used: 0.0
    cost_currency: USD

constraints:
  - no_external_action

escalation_target: thomas_prime
trial_authorization_ref: null
expires_at: "2026-07-10T09:30:00Z"
created_at: "2026-07-10T09:00:00Z"
```

## 12. Invalidation

Issue a new Assignment when any of the following changes:

- Role ID or Role Version.
- Assignment mode.
- Role objective or material input.
- Assigned or excluded capability.
- Active Core scope.
- Memory scope.
- Required, granted, or effective authority.
- Permission Decision or approval binding.
- Tool or Program scope.
- Validation level.
- Budget increase.
- Candidate trial authorization.
- Target, content, amount, Tool, or scope of an approved external action.
