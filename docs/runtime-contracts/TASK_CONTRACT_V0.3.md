# Task Contract v0.3

**Schema Version:** `task.v0.3`
**Document Version:** `0.3.0`
**Status:** `Active MVP Contract`
**Owner:** `Thomas`

**Authority Model:** [`AUTHORITY_AND_PERMISSION_MODEL.md`](./AUTHORITY_AND_PERMISSION_MODEL.md)
**Execution Budget:** [`EXECUTION_BUDGET_SCHEMA.yaml`](./EXECUTION_BUDGET_SCHEMA.yaml)
**Role Assignment:** [`../../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md`](../../03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md)
**Agent Output:** [`AGENT_OUTPUT_CONTRACT_V0.2.md`](./AGENT_OUTPUT_CONTRACT_V0.2.md)

## 1. Purpose

Task is the universal work unit of Thomas Autonomous Organization.

Every user request, scheduled job, system-generated work item, Agent-created Subtask, approval follow-up, retry, and recovery action must belong to one Task.

Task v0.3 separates:

```text
Core Context Binding
→ Exact approved Core Release governing the Task revision
```


```text
Goal
Classification
Authority Requirement
Permission Decision
Routing
Role Assignment
Validation
Execution Budget
Lifecycle
Result
Audit
```

The Task contract does not grant Role authority by itself.

A Role executes only through a valid `role_assignment.v0.2`.

## 2. Core Design Principles

1. One Task has one primary objective.
2. A Task may have multiple Role Assignments, but each Assignment owns a distinct Role scope.
3. Complexity and risk are separate axes.
4. P0–P6 Authority and `ALLOW | EXECUTE_AND_REPORT | APPROVAL_REQUIRED | BLOCK` Permission Decisions are separate axes.
5. Permission Decision is action-specific and does not increase authority.
6. Task budget is the total Task envelope. Assignment budgets are subdivisions of the remaining Task budget.
7. Subtasks inherit Trace, Core, risk lower bound, constraints, authority limits, approval limits, and remaining budget.
8. A material change invalidates affected Permission Decisions, approvals, and Role Assignments.
9. Task state transitions are validated against the canonical state machine.
10. Every meaningful transition, decision, approval, execution, result, and Memory action is auditable.

## 3. Top-Level Structure

```yaml
schema_version: task.v0.3

identity: {}
source: {}
request: {}
scope: {}
classification: {}
authority: {}
permission: {}
routing: {}
context: {}
validation: {}
execution_budget: {}
results: {}
lifecycle: {}
audit: {}
```

## 4. Identity

```yaml
identity:
  task_id: task_01HX_example
  trace_id: trace_01HX_example
  root_task_id: task_01HX_example
  parent_task_id: null
  task_revision: 1
```

Rules:

- A Task may keep `core_context_binding_id: null` only while its lifecycle status is `RECEIVED`.
- Before classification, planning, routing, or execution, Runtime creates Binding v0.3 from the Task file and writes the exact Binding ID back to the Task.
- Runtime Rule membership is resolved through the bound Release snapshot, not the current working-tree Active Core.
- `task_id` is immutable.
- `trace_id` is shared by the root Task and every Subtask in the same end-to-end flow.
- `root_task_id` points to the root Task.
- `parent_task_id` is required for a Subtask.
- `task_revision` increases when a material Task plan or scope change occurs.
- A terminal Task is not reopened. Re-execution after `FAILED`, `CANCELED`, or `CLOSED` requires a new Task ID.

## 5. Source and Request

```yaml
source:
  channel: telegram
  source_ref: telegram_message:12345
  requester:
    requester_type: real_thomas
    requester_id: thomas
    authenticated: true

request:
  raw_request: "이 자료를 분석하고 핵심 위험을 알려줘"
  normalized_goal: "Analyze the supplied material and identify material risks."
  received_at: "2026-07-10T09:00:00Z"
```

Allowed `source.channel` values:

```text
telegram
scheduler
agent
system
api
manual
```

Authentication does not automatically grant action permission.

## 6. Scope

```yaml
scope:
  primary_objective: Analyze the supplied material and identify material risks.
  success_conditions:
    - material_findings_present
    - material_risks_disclosed
  constraints:
    - no_external_action
  exclusions:
    - source_verification_outside_assigned_context
  expected_outputs:
    - structured_analysis
    - risk_summary
```

Task scope defines the whole Task.

Role Assignment defines each Role's exact subset.

Task and Role scope must not be treated as the same object.

## 7. Classification

```yaml
classification:
  classification_status: CLASSIFIED
  execution_mode: AGENT
  complexity: NORMAL
  priority: NORMAL
  risk_level: GREEN
  classification_reasons:
    - judgment_is_required
    - output_is_internal_and_reversible
```

Allowed values:

```text
classification_status:
UNCLASSIFIED
CLASSIFIED

execution_mode:
PROGRAM
AGENT
HYBRID

complexity:
SIMPLE
NORMAL
COMPLEX

priority:
LOW
NORMAL
HIGH
URGENT

risk_level:
GREEN
YELLOW
ORANGE
RED
```

Complexity does not imply risk.

Examples:

```text
Complex internal strategy analysis
→ COMPLEX + GREEN or YELLOW

Simple external message send
→ SIMPLE + ORANGE
```

## 8. Authority

```yaml
authority:
  required_permission_level: P2
  authority_reason: Analysis is required.
```

Allowed levels:

```text
P0
P1
P2
P3
P4
P5
P6
```

`required_permission_level` means the minimum authority required by the planned Task action.

It is not a Permission Decision.

Before classification and planning, it may be `null`.

Before a Task enters `AUTHORIZING`, it must be resolved.

## 9. Permission

```yaml
permission:
  evaluation_status: DECIDED
  permission_decision: ALLOW
  permission_decision_ref: perm_01HX_example
  approval_state: NOT_REQUIRED
  approval_id: null
  action_fingerprint: null
```

Allowed values:

```text
evaluation_status:
NOT_EVALUATED
DECIDED
SUPERSEDED

permission_decision:
ALLOW
EXECUTE_AND_REPORT
APPROVAL_REQUIRED
BLOCK

approval_state:
NOT_REQUIRED
PENDING
APPROVED
REJECTED
EXPIRED
CONSUMED
```

Rules:

- `permission_decision` is `null` while `evaluation_status` is `NOT_EVALUATED`.
- `permission_decision_ref` is required when `evaluation_status` is `DECIDED`.
- `APPROVAL_REQUIRED` requires an exact `approval_id` and `action_fingerprint`.
- Approval does not increase P-level authority.
- A changed target, content, amount, Tool, Program, data scope, or permission scope supersedes the old Permission Decision and invalidates the approval.
- `SUPERSEDED` decisions cannot authorize execution.

## 10. Routing

```yaml
routing:
  required_capabilities:
    - analysis
  selected_route: ROLE
  assigned_role_ids:
    - general.specialist
  assigned_actor_ids:
    - agent_instance_01HX_example
  role_assignment_ids:
    - assignment_01HX_example
  program_request_ids: []
  tool_request_ids: []
```

Allowed `selected_route` values:

```text
UNASSIGNED
PROGRAM
ROLE
HYBRID
```

Rules:

- `PROGRAM` is preferred when deterministic processing is sufficient.
- A Role requires a valid Role Registry entry and Role Assignment.
- Candidate Roles are prohibited in normal routing.
- Candidate trials require explicit `candidate_trial` Assignment mode and authorization.
- Routing records do not grant Tool or Program permission.

## 11. Context

```yaml
context:
  core_context_binding_id: ccb-task-example-001
  input_refs:
    - task.request.raw_request
  context_refs: []
  active_core_rule_ids:
    - MVP_RULE_006
    - MVP_RULE_008
  memory_refs: []
  data_sensitivity: INTERNAL
```

Allowed `data_sensitivity` values:

```text
PUBLIC
INTERNAL
PRIVATE
SENSITIVE
RESTRICTED
```

Rules:

- `core_context_binding_id` is required for every Runtime Task revision.
- The Binding must reference one exact approved Core Release.
- `active_core_rule_ids` must be explicit, non-empty, and a subset of the Binding's Active Rule IDs.
- Rule ID syntax is generic; actual membership is validated against the approved Core Release Manifest.
- A Rule ID alone is not enough for exact replay. Exact meaning requires the Core Release and Active Core hash.
- Context Reference does not automatically grant unrestricted Memory access.
- A Role may access only the context and Memory explicitly allowed in its Role Assignment.
- Restricted data requires explicit access policy and must not be exposed through Agent Output or Memory Candidate.

## 12. Validation

```yaml
validation:
  mode: AUTOMATIC
  status: NOT_STARTED
  acceptance_criteria:
    - objective_met
    - output_contract_valid
    - material_uncertainty_disclosed
  rejection_criteria:
    - material_evidence_misrepresentation
    - authority_or_permission_violation
  validation_output_refs: []
```

Allowed modes:

```text
AUTOMATIC
INDEPENDENT
RISK_REVIEW
```

Allowed status values:

```text
NOT_REQUIRED
NOT_STARTED
IN_PROGRESS
PASS
REVISE
BLOCK
```

Rules:

- Effective validation uses the highest requirement from Policy, Task, Role Definition, and Role Assignment.
- The creator cannot count self-review as independent validation.
- Validation does not grant Permission.
- Required evidence missing should normally produce a formal `BLOCK`, not silent validation omission.

## 13. Execution Budget

Task uses `execution_budget.v0.1`.

```yaml
execution_budget:
  schema_version: execution_budget.v0.1
  limits:
    max_agent_invocations: 3
    max_model_calls: 12
    max_tool_calls: 20
    max_program_calls: 10
    max_revision_cycles: 2
    max_validation_cycles: 2
    max_retry_count: 3
    max_parallel_workers: 3
    max_runtime_seconds: 1800
    token_budget: 50000
    cost_budget: 5.0
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
```

Rules:

- Task budget is the total Task envelope.
- Every Role Assignment budget must fit within the remaining Task budget.
- Assignment creation does not create new budget.
- Subtask creation does not create new budget.
- Usage is append-only or event-derived. It must not be silently reset.
- Budget exhaustion blocks new Agent, Tool, and Program calls.

## 14. Results

```yaml
results:
  agent_output_refs: []
  program_result_refs: []
  validation_output_refs: []
  final_output_ref: null
  partial_completion:
    is_partial: false
    completed_scope: []
    missing_scope: []
    impact: []
    next_action: null
```

Rules:

- `COMPLETED` requires a result reference or an explicitly declared partial result.
- Partial completion must disclose missing scope, impact, and next action.
- Result references preserve the schema version used by the historical record.

## 15. Lifecycle

```yaml
lifecycle:
  status: RECEIVED
  previous_status: null
  status_reason: Task received.
  blocked_reason: null
  pause_resume_target: null
  transition_event_ref: audit_01HX_example
  status_entered_at: "2026-07-10T09:00:00Z"
```

Canonical states:

```text
RECEIVED
CLASSIFIED
PLANNED
AUTHORIZING
WAITING_APPROVAL
QUEUED
RUNNING
VALIDATING
REVISING
RETRYING
PAUSED
BLOCKED
FAILED
CANCELED
COMPLETED
MEMORY_REVIEW
CLOSED
```

Canonical transitions are defined in:

```text
TASK_STATE_MACHINE_V0.1.yaml
```

A state value alone is not enough. The transition must also satisfy its guard conditions.

## 16. Audit

```yaml
audit:
  created_by: telegram_gateway
  created_at: "2026-07-10T09:00:00Z"
  updated_at: "2026-07-10T09:00:00Z"
  audit_refs:
    - audit_01HX_example
```

Audit must cover at least:

- Task creation.
- Normalization.
- Classification.
- Planning.
- Authority requirement.
- Permission Decision.
- Approval request, decision, expiration, and consumption.
- Routing.
- Role Assignment.
- Program and Tool requests.
- State transition.
- Retry, pause, block, cancellation, and failure.
- Validation.
- Result finalization.
- Memory Review.
- Task close.

Audit is append-only.

## 17. Subtask Inheritance

A Subtask must inherit:

```text
trace_id
root_task_id
parent_task_id
Active Core constraints
user constraints
risk lower bound
authority upper bound
approval limits
remaining budget
Audit linkage
```

A Subtask cannot:

- Reduce inherited risk without a new documented Policy evaluation.
- Expand authority.
- Escape a prohibited action.
- Broaden an approval.
- Reset budget usage.
- Use a Candidate Role through normal routing.
- Load unrelated Context or Memory.

## 18. Material Change and Revision

A material Core rebind requires a new Task revision, a new `core_context_binding_id`, replanning, reauthorization, and invalidation of affected Assignments, Permission Decisions, and Approvals.

A running Task cannot silently move to a newer Core Release.



The following changes are material:

- Primary objective.
- Material success condition.
- Important input.
- External target.
- Content to be published or sent.
- Amount or financial scope.
- Tool or Program.
- Data sensitivity or access scope.
- Required authority.
- Permission scope.
- Active Core scope.
- Validation requirement.
- Budget increase.

A material change requires:

```text
task_revision + 1
↓
affected Permission Decision -> SUPERSEDED
↓
affected approval -> invalid
↓
affected Role Assignment -> invalid
↓
Task returns to PLANNED or AUTHORIZING
↓
new records are issued
```

Historical records are preserved.

## 19. State-Specific Invariants

### `RECEIVED`

- Classification may be `UNCLASSIFIED`.
- Authority may be unresolved.
- Permission must be `NOT_EVALUATED`.

### `CLASSIFIED`

- Classification must be complete.
- Risk and complexity must be present.

### `PLANNED`

- Scope, success conditions, required outputs, and required authority must be resolved.
- Numeric execution budget must be present.

### `AUTHORIZING`

- Required authority must be present.
- Planned action scope must be stable enough to fingerprint when approval may be needed.

### `WAITING_APPROVAL`

Must have:

```text
permission_decision: APPROVAL_REQUIRED
approval_state: PENDING
approval_id
action_fingerprint
```

### `QUEUED` and `RUNNING`

Must have an executable Permission state:

```text
ALLOW

or

EXECUTE_AND_REPORT

or

APPROVAL_REQUIRED + APPROVED/CONSUMED
```

### `BLOCKED`

Must include `blocked_reason`.

### `PAUSED`

Must preserve the pre-pause state and an allowed resume target.

### `COMPLETED`

Must include final or partial result evidence.

### `MEMORY_REVIEW`

Task execution is finished. Only Memory review and final Audit completion may continue.

### `CLOSED`

- Final output and final Task status are recorded.
- Memory result is recorded.
- Required Audit refs are present.
- Task is terminal.

## 20. Example

See:

```text
examples/tasks/task_v0.3_internal_analysis.yaml
examples/tasks/task_v0.3_waiting_approval.yaml
```

## 21. Final Rule

> Task owns the total objective, risk, authority requirement, Permission state, total budget, lifecycle, results, and Audit lineage.

> Role Assignment owns one Role's exact execution scope.

> Agent Output owns one Role execution result.

> Approval authorizes one exact action.

> None of these objects may silently expand another object's authority or scope.
