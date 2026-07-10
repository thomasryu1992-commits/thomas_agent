# Role Assignment Contract

**Schema Version:** `role_assignment.v0.1`
**Status:** `MVP Draft`
**Owner:** `Thomas`

**Role Standard:** [MVP Dynamic Role Contract](./MVP_DYNAMIC_ROLE_CONTRACT.md)

**Task and Agent Output Contracts:** [Thomas Twin Core Architecture](../docs/thomas-twin-core-architecture-v0.1.md)

## 1. Purpose

Role Assignment는 특정 Task에서 Dynamic Role에 실제로 부여된 목표, Context, 권한, Program, Tool과 실행 예산을 기록한다.

Role Definition만으로 Role을 실행할 수 없다.

## 2. Runtime Position

```text
Task v0.2
↓
Thomas Prime selects an active Role
↓
Policy and Budget checks
↓
Role Assignment v0.1
↓
Role execution
↓
Agent Output v0.1
```

## 3. Required Fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | `role_assignment.v0.1` |
| `assignment_id` | string | Unique assignment ID |
| `trace_id` | string | End-to-end trace ID |
| `task_id` | string | Related Task v0.2 |
| `parent_task_id` | string or null | Parent Task when this is a Subtask |
| `role_id` | string | Selected Role Registry ID |
| `role_version` | string | Exact Role Definition version |
| `role_definition_ref` | string | Role Definition path or registry reference |
| `actor_instance_id` | string | Runtime Agent instance |
| `assigned_by` | string | Usually Thomas Prime |
| `assignment_status` | string | Assignment runtime state |
| `objective_ref` | string | Reference to Task normalized goal |
| `input_refs` | array | Inputs allowed for this assignment |
| `context_refs` | array | Memory, document and prior output references |
| `active_core_rule_ids` | array | Active Core rules relevant to this Task |
| `constraints` | array | Hard limits and prohibited behavior |
| `allowed_program_ids` | array | Effective Program allowlist |
| `allowed_tool_ids` | array | Effective Tool allowlist |
| `role_permission_ceiling` | string | Maximum permission from Role Definition |
| `task_permission_decision` | string | Task-level Permission Decision |
| `effective_permission` | string | Final permission after all checks |
| `validation_mode` | string | `automatic`, `independent`, or `risk_review` |
| `execution_budget` | object | Numeric budget allocated to this assignment |
| `escalation_target` | string | Always `thomas_prime` in MVP |
| `expires_at` | string | Assignment expiration time |
| `created_at` | string | UTC timestamp |

## 4. Assignment Status

Assignment status is separate from Task status and Agent Output status.

```text
ASSIGNED
RUNNING
OUTPUT_READY
BLOCKED
FAILED
CANCELED
EXPIRED
```

## 5. Permission Rule

```text
effective_permission =
Role permission ceiling
∩ Task permission
∩ Assignment permission
∩ Tool or Program scope
∩ Policy Engine decision
```

Role은 Assignment에 기록되지 않은 권한을 사용할 수 없다.

Policy Engine의 결과가 바뀌면 Assignment를 다시 발급한다.

## 6. Budget Rule

```text
effective_budget =
minimum(
  Operating Policy limit,
  Parent Task remaining budget,
  Role Definition cap,
  Assignment allocation
)
```

`execution_budget`의 모든 값은 실행 시 숫자로 지정해야 한다.

Subtask나 새 Assignment를 생성해 상위 Task의 남은 예산을 늘릴 수 없다.

예산 확장은 Thomas의 Task별 승인을 받은 새 Assignment로만 가능하다.

## 7. Example

```yaml
schema_version: role_assignment.v0.1
assignment_id: assignment_01HX_example
trace_id: trace_01HX_example
task_id: task_01HX_example
parent_task_id: null
role_id: general.specialist
role_version: 0.1.0
role_definition_ref: role_registry:general.specialist@0.1.0
actor_instance_id: agent_instance_01HX_example
assigned_by: thomas_prime
assignment_status: ASSIGNED
objective_ref: task.normalized_goal
input_refs:
  - task.raw_request
context_refs: []
active_core_rule_ids:
  - MVP_RULE_006
  - MVP_RULE_008
constraints:
  - no_external_action
allowed_program_ids: []
allowed_tool_ids:
  - approved_document_reader
role_permission_ceiling: P3
task_permission_decision: ALLOW
effective_permission: P2
validation_mode: automatic
execution_budget:
  model_calls: 3
  tool_calls: 5
  revision_cycles: 1
  validation_cycles: 1
  retries: 1
  parallel_workers: 1
  runtime_seconds: 600
  token_budget: 20000
  cost_budget: 1.0
  cost_currency: USD
escalation_target: thomas_prime
expires_at: "2026-07-10T09:30:00Z"
created_at: "2026-07-10T09:00:00Z"
```

Example budget values are illustrative and do not define the production server defaults.

## 8. Invalidation Rules

다음 변경이 발생하면 기존 Assignment를 무효화하고 새로 발급한다.

- Role ID 또는 Role Version 변경
- 목표 또는 중요 입력 변경
- Active Core Scope 변경
- 권한 또는 Tool Scope 변경
- Validation 수준 변경
- 예산 확장
- 기존 승인에 묶인 행동의 대상, 내용, 금액 또는 Tool 변경
