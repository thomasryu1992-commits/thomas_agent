# MVP Dynamic Role Contract

**Document Version:** `0.2.0`
**Document Status:** `Reviewed MVP Contract`
**Owner:** `Thomas`
**Applies To:** Dynamic Specialist, Independent Validator, Risk Reviewer

**Operating Policy:** [MVP Operating Policy](../docs/MVP_OPERATING_POLICY.md)

**Common I/O Contracts:** [Thomas Twin Core Architecture](../docs/thomas-twin-core-architecture-v0.1.md)

**Prime Authority:** [Thomas Prime Charter](./THOMAS_PRIME_CHARTER.md)

## 1. Purpose

본 문서는 MVP에서 사용하는 모든 Dynamic Role의 공통 계약 규칙을 정의한다.

특정 역할의 전문 내용은 `ROLES/` 아래 Role Definition에서 정의한다.

기계가 읽는 필드 구조는 `ROLE_DEFINITION_TEMPLATE.yaml`을 사용한다.

특정 Task의 실제 권한, 입력, Context와 예산은 `ROLE_ASSIGNMENT_CONTRACT.md`를 사용한다.

Thomas Prime은 Dynamic Role이 아니며 본 계약을 상속하지 않는다.

## 2. Contract Layers

```text
MVP Dynamic Role Contract
→ 모든 Dynamic Role의 공통 규칙

Role Definition
→ 역할의 지속적인 목적, Capability와 권한 상한

Role Assignment
→ 특정 Task에서 실제로 부여된 입력, 권한, Tool과 예산

Agent Output
→ Role 실행 결과
```

Role Definition과 Role Assignment가 모두 유효해야 실행할 수 있다.

## 3. Document Precedence

다음 우선순위를 적용한다.

```text
Active Thomas Core
↓
MVP Operating Policy
↓
Common I/O Contracts
↓
Thomas Prime Charter
↓
MVP Dynamic Role Contract
↓
Role Definition
↓
Role Assignment and Task Instructions
↓
Runtime Defaults
```

Thomas의 일반 Task 지시는 위 문서의 Core, 금지, 권한, 위험과 실행 한도 규칙을 암묵적으로 변경하지 않는다.

Core나 Policy를 변경하려면 해당 문서의 명시적 변경 절차를 사용한다.

충돌을 안전하게 해결할 수 없으면 실행을 중지하고 Thomas Prime에게 전달한다.

비활성 문서는 실행 근거로 사용하지 않는다.

## 4. Role Definition Requirements

모든 Role Definition은 다음 정보를 가져야 한다.

- `schema_version`
- `role_id`
- `role_name`
- `role_version`
- `status`
- `routable`
- `role_type`
- `purpose`
- `capabilities`
- `activation_conditions`
- `non_activation_conditions`
- `input_contract`
- `active_core`
- `permission_ceiling`
- `allowed_program_ids`
- `allowed_tool_ids`
- `memory_policy`
- `output_contract`
- `validation_policy`
- `budget_caps`
- `stop_conditions`
- `completion_criteria`
- `quality_criteria`
- `change_control`

MVP에서 허용하는 `role_type`은 다음과 같다.

```text
dynamic_specialist
independent_validator
risk_reviewer
```

Program Operator와 Department Head는 Dynamic Role이 아니며 별도 계약이 필요하다.

## 5. Role Status and Activation

Role 상태는 다음 중 하나다.

```text
draft
candidate
active
disabled
deprecated
archived
```

Prime은 `ROLE_REGISTRY.yaml`에서 `active`이면서 `routable: true`인 역할만 자동 선택할 수 있다.

역할 선택 순서는 다음과 같다.

1. Task에 필요한 Capability를 모두 충족한다.
2. Task에 필요한 최소 Permission으로 수행할 수 있다.
3. Program만으로 충분하면 Role을 활성화하지 않는다.
4. 충분한 역할 중 더 좁은 범위와 낮은 비용을 우선한다.
5. 품질 차이가 의미 있을 때만 더 전문적인 역할을 선택한다.

Role은 완료, 취소, 한도 도달, 권한 초과, 필요성 소멸 시 비활성화한다.

## 6. Input Contract

Dynamic Role의 공통 입력은 다음 두 계약이다.

```text
Task v0.2
+
Role Assignment v0.1
```

Role Definition은 `objective`, `expected_output`, `risk_level` 등 Task 필드를 별도로 재정의하지 않는다.

Role은 Assignment에 포함된 `input_refs`와 `context_refs`만 사용할 수 있다.

필수 입력이 부족한 경우 다음을 따른다.

- 영향이 작으면 안전한 가정을 사용하고 Agent Output에 명시한다.
- 결과에 중요한 영향을 주면 실행을 중지하고 Prime에 입력 보완을 요청한다.
- 위험한 추정이나 새로운 권한이 필요하면 실행하지 않고 Prime에 전달한다.

## 7. Active Core Scope

Role은 Active Core 전체를 임의로 읽거나 선택하지 않는다.

Prime은 Role Assignment에 Task와 관련된 `active_core_rule_ids`를 전달한다.

MVP의 Rule ID는 `MVP_RULE_001`부터 `MVP_RULE_008` 범위에서 선택한다.

reference-only Core와 비활성 Core Candidate는 명시적인 Context Reference가 없으면 사용할 수 없다.

Role은 전달받은 Core 규칙을 해석하거나 적용할 수 있지만 변경하거나 확장할 수 없다.

## 8. Capability, Tool, and Permission

Role은 자신의 Definition에 등록된 Capability만 수행한다.

Program과 Tool은 다음 조건을 모두 만족해야 한다.

- Registry에 등록되고 활성 상태다.
- Role Definition의 Allowlist에 포함된다.
- Role Assignment의 Allowlist에 포함된다.
- Task와 Assignment의 Permission 범위 안에 있다.
- Policy Engine의 Permission Decision을 통과한다.

실제 권한은 다음 교집합으로 결정한다.

```text
effective_permission =
Role permission ceiling
∩ Task permission
∩ Role Assignment permission
∩ Tool or Program scope
∩ Policy Engine decision
```

Role은 권한을 스스로 상향하거나 다른 Role에 위임할 수 없다.

Dynamic Role의 기본 외부 행동 권한은 `false`다.

## 9. Memory Policy

Role은 Assignment에 포함된 Memory Scope만 조회할 수 있다.

기본 원칙은 다음과 같다.

- 관련 Validated Memory와 Task Working Memory만 조회한다.
- 관련 없는 개인 Memory와 비활성 Core Candidate를 조회하지 않는다.
- Validated Memory를 직접 생성, 수정, 덮어쓰기 또는 삭제하지 않는다.
- 재사용 가치가 있는 정보는 `memory_candidates`로만 제안한다.
- 비밀번호, API Key, 인증 Token, 복구 코드는 Memory Candidate로 만들지 않는다.

Memory Candidate는 공통 Memory Record 승격 절차를 따른다.

## 10. Output Contract

모든 Dynamic Role 결과는 `agent_output.v0.1`을 그대로 사용한다.

Role Definition은 Agent Output의 공통 필드와 상태를 재정의하지 않는다.

역할별 필드는 `role_specific_output` 안에서만 확장한다.

Agent Output 상태는 다음과 같다.

```text
draft
final
needs_validation
rejected
```

Task나 Role Assignment의 실행 상태를 Agent Output 상태로 사용하지 않는다.

Role은 사실, 근거, 추론, 가정, 불확실성과 충돌 정보를 구분한다.

부분 결과를 반환할 때는 누락 항목, 영향과 다음 행동을 명시한다.

## 11. Validation Policy

실제 Validation 수준은 다음 중 가장 높은 요구 수준을 사용한다.

```text
effective_validation =
max(Operating Policy, Task, Role Definition, Role Assignment)
```

Role Definition은 Policy나 Task가 요구하는 검증 수준을 낮출 수 없다.

독립 Validation은 별도 Agent 인스턴스 또는 새로운 실행 문맥에서 수행한다.

생성 Role은 자신의 결과를 독립 검증한 것으로 처리할 수 없다.

Validation 결과는 `PASS`, `REVISE`, `BLOCK` 중 하나다.

수정과 검증 횟수는 실제 실행 예산을 넘을 수 없다.

## 12. Execution Budget

Role Definition의 Budget은 추가 예산이 아니라 역할별 절대 상한이다.

실제 예산은 다음 교집합으로 결정한다.

```text
effective_budget =
minimum(
  Operating Policy limit,
  Parent Task remaining budget,
  Role Definition cap,
  Role Assignment allocation
)
```

Role Assignment에는 다음 값이 숫자로 지정되어야 한다.

- Agent 또는 모델 호출 수
- Tool 호출 수
- 수정 횟수
- 검증 횟수
- 재시도 횟수
- 병렬 Worker 수
- 실행 시간
- Token 예산
- 비용 예산과 통화

필수 예산 값이 없으면 Role을 실행하지 않는다.

Role은 Subtask 또는 다른 Role을 생성해 한도를 우회할 수 없다.

예산 확장이 필요하면 실행을 중지하고 Prime에 전달한다. Thomas의 Task별 승인 없이는 예산을 늘릴 수 없다.

## 13. Stop, Failure, and Escalation

다음 조건에서는 즉시 중지한다.

- 금지 행동 또는 권한 초과가 필요하다.
- 등록되지 않은 Program 또는 Tool이 필요하다.
- 보안, 민감 정보 노출 또는 데이터 손상 가능성이 있다.
- 승인 범위 또는 실행 예산을 초과한다.
- Active Core 또는 Policy와 해결할 수 없는 충돌이 있다.

권한, 보안, 데이터 손상, 고위험 외부 행동 실패는 자동 재시도하지 않는다.

Dynamic Role의 에스컬레이션 대상은 항상 Thomas Prime이다.

Role은 Thomas에게 직접 메시지를 보내거나 직접 승인을 요청하지 않는다.

Prime은 Operating Policy에 따라 Thomas 보고 또는 승인을 결정한다.

## 14. Completion and Quality

Role Definition은 완료 기준과 품질 기준을 분리한다.

완료 기준은 요구 산출물의 존재와 Task 완료 가능성을 판단한다.

품질 기준은 목표 정렬, 근거, 논리, 불확실성, Core 적합성과 권한 준수를 판단한다.

완료 기준을 충족해도 품질 기준에 미달하면 `needs_validation` 또는 수정 요청으로 반환할 수 있다.

## 15. Change Control

Role Definition 변경은 Semantic Versioning을 사용한다.

Agent와 Role은 자신의 Definition, Permission, Tool Allowlist 또는 Budget Cap을 직접 변경할 수 없다.

Prime과 Agent는 변경 후보를 제안할 수 있다.

낮은 위험의 제한 시험은 MVP Operating Policy의 Learning 조건을 따른다.

Role 활성화, 공통 Capability 변경, 권한 상한 변경과 조직 전체 라우팅 변경은 Thomas 승인을 요구한다.

## 16. Final Principles

> Dynamic Role은 필요한 Task에서만 활성화한다.

> Role Definition은 지속적인 능력 상한을, Role Assignment는 이번 Task의 실제 범위를 정의한다.

> Program으로 충분한 업무에는 Agent Role을 사용하지 않는다.

> 모든 결과는 공통 Agent Output 계약을 따른다.

> Role은 자신의 권한, Core, Validated Memory와 실행 예산을 직접 확대할 수 없다.

> 실패, 부분 결과와 불확실성을 숨기지 않는다.
