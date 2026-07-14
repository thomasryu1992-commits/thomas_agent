# Thomas Agent Architecture Slimming Principles

- Document ID: `THOMAS_AGENT_ARCHITECTURE_SLIMMING_PRINCIPLES`
- Version: `0.1.0`
- Status: `ACTIVE_ARCHITECTURE_REVIEW_BASELINE`
- Baseline: `I0.5.5`
- Scope: Thomas Agent MVP architecture
- Purpose: 구조적 안전성을 유지하면서 시스템의 활성 골격을 다시 얇게 만들기 위한 기준 문서

---

## 1. 배경

Thomas Agent의 초기 목표는 다음과 같다.

> 구조는 탄탄하되 최대한 얇게 유지한다.

초기 설계의 핵심은 Thomas Core를 중심으로 Thomas Prime, General Specialist, Independent Validation, Program/Tool Registry, Memory, Audit가 연결되는 최소 자율형 에이전트 골격이었다.

개발이 진행되면서 Permission, Approval, Runtime Readiness, Entry Plan, Entry Authorization, At-most-once Transition, Durable State, CAS, Recovery, Disabled Entry, Monitoring, Alert, Supervisor, Scheduler, Sandbox와 같은 안전 조건이 각각 독립 Contract, Schema, Registry, Validator, Fixture, Gate로 확장되었다.

이 과정은 안전성을 강화했지만 다음 구조적 비용을 만들었다.

1. 하나의 의미가 여러 문서와 Registry에 반복 정의된다.
2. 중복 정의의 일치 여부를 검증하기 위한 Validator가 추가된다.
3. Validator를 검증하기 위한 Fixture와 Release Gate가 추가된다.
4. 미래 Runtime 조건이 현재 Active Architecture보다 커진다.
5. 조건 하나가 새로운 시스템 구성 요소 하나로 승격된다.

본 문서는 이러한 확장을 중단하고, Thomas Agent를 다시 **강한 중심과 얇은 Runtime 허리**를 가진 구조로 정리하기 위한 최상위 Slimming 원칙을 정의한다.

---

## 2. Step 1 선언: Architecture Freeze

I0.5.5를 Architecture Slimming 이전의 기준점으로 동결한다.

```yaml
architecture_baseline:
  baseline_id: THOMAS_AGENT_I0_5_5_PRE_SLIMMING
  status: FROZEN_FOR_ARCHITECTURE_REVIEW
  purpose:
    - 현재 구조 보존
    - Slim Architecture 전환 기준점 확보
    - 기존 안전 경계 유지

  temporarily_prohibited:
    - new_governance_contract
    - new_review_only_boundary
    - new_disabled_component
    - new_phase_specific_registry
    - new_phase_specific_validator
    - new_phase_specific_release_gate

  allowed_exceptions:
    - critical_security_fix
    - data_corruption_fix
    - existing_test_failure_fix
    - slimming_required_refactor
```

Architecture Slimming이 완료될 때까지 I0.5.6 이상의 새로운 Governance 확장은 진행하지 않는다.

기존 안전 구조는 즉시 삭제하지 않는다. 먼저 분류하고, Source of Truth를 확정한 뒤 Active / Deferred / Historical 경계를 재설정한다.

---

## 3. 최상위 목표 구조

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
Thin Runtime Kernel
  ↓
Router
  ↓
Agent / Program / Tool
  ↓
Validation
  ↓
Memory / Audit
```

각 계층의 책임은 다음과 같다.

| Layer | Primary responsibility |
|---|---|
| Thomas | 최종 주권과 명시적 승인 |
| Thomas Core | 정체성, 가치, 장기 목표, 판단 기준 |
| System Constitution | 시스템 전체에 적용되는 최소 불변 원칙 |
| Governance Policy | 위험, 권한, 승인, 효과 판단 |
| Thomas Prime | 목표 해석, 계획, 라우팅, 조정 |
| Runtime Kernel | Task lifecycle 실행 |
| Router | Role / Program / Tool 선택 |
| Agent / Program / Tool | 실제 Capability 수행 |
| Validation | 결과 검증과 독립 평가 |
| Memory / Audit | 학습 후보, 검증된 기억, 추적 기록 |

Thomas Prime은 전체 정책의 소유자가 아니다.

```text
Core       → 방향
Governance → 권한
Prime      → 조정
Runtime    → 실행
Validation → 검증
Memory     → 축적
```

---

## 4. Architecture Slimming 핵심 원칙

### Principle 1. One Concept, One Source of Truth

하나의 개념은 하나의 Source of Truth만 가진다.

```text
Thomas Identity
→ Thomas Core

System-wide Principles
→ System Constitution

Risk / Permission / Approval
→ Governance Policy

Role Capability
→ Role Definition

Role Activation Status
→ Role Registry

Task Structure
→ Task Contract

Runtime Behavior
→ Runtime Kernel
```

다른 문서, Registry, Runtime은 동일 내용을 복사하지 않고 Source ID, Path, Version, Hash로 참조한다.

### Principle 2. A Condition Is Not Automatically a Contract

새로운 조건은 기본적으로 새로운 Contract가 아니다.

기존 확장 패턴:

```text
New Condition
→ New Contract
→ New Schema
→ New Registry
→ New Validator
→ New Fixture
→ New Gate
```

목표 패턴:

```text
New Condition
→ Governance Rule
→ Existing Policy Engine
→ Existing Test Harness
```

새 Contract는 새로운 독립 책임, 독립 Lifecycle, 독립 저장 Record 또는 프로세스 간 Interface가 생길 때만 허용한다.

### Principle 3. Policy and Runtime Must Be Separate

Policy는 무엇이 허용되는지 판단하고 Runtime은 그 결과를 집행한다.

```text
Task / Action
    ↓
Governance.evaluate(...)
    ↓
ALLOW / REDUCE / APPROVAL_REQUIRED / BLOCK
    ↓
Runtime execution
```

권장 인터페이스:

```python
decision = governance.evaluate(
    actor=actor,
    action=action,
    target=target,
    context=context,
)
```

권장 결과 구조:

```yaml
decision:
  result: ALLOW
  risk: GREEN
  approval_required: false
  allowed_effects:
    - internal_read
  blocking_reasons: []
  applied_rule_ids:
    - GOV_001
```

Runtime Kernel은 개별 Permission, Approval, Effect Rule의 Source of Truth가 되어서는 안 된다.

### Principle 4. Registry Is an Index, Not a Definition

Registry는 Component를 찾고 활성 상태를 확인하기 위한 Index다.

Role Registry의 권장 최소 구조:

```yaml
role_id: general.specialist
version: 0.3.0
status: active
routable: true
definition_path: 03_ROLE_CONTRACTS/ACTIVE/GENERAL_SPECIALIST_ROLE.md
definition_sha256: <sha256>
```

다음 정보는 Role Registry에 복제하지 않는다.

- capability definitions
- permission ceilings
- restrictions
- validation policy
- promotion requirements
- detailed role behavior

이 정보는 Role Definition에서만 관리한다.

### Principle 5. Future Capability Is a Deferred Requirement

미래에 필요할 수 있다는 이유만으로 현재 Active Architecture에 독립 Component를 추가하지 않는다.

다음 항목은 실제 활성 시점 전까지 Deferred Requirement로 관리할 수 있다.

- Runtime-authoritative entry
- Approval consumption
- At-most-once execution
- Durable CAS
- Crash recovery
- Executor enablement
- Monitoring
- Alerting
- Health checks
- Clock synchronization
- Kill switch
- Process supervisor
- Scheduler
- Sandbox

Deferred는 삭제가 아니다. Deferred 문서는 요구사항, 활성 조건, 필요 시점, 안전 메모를 보존한다.

### Principle 6. Create a Schema Only for Runtime Data Boundaries

새 Schema는 다음 경우에만 만든다.

1. Runtime이 독립적으로 저장하는 Record
2. 프로세스 또는 Component 사이에서 전달되는 Interface
3. 장기간 호환성을 유지해야 하는 외부 Data Contract
4. 독립적인 검증 및 Lifecycle이 필요한 데이터 객체

MVP Active Runtime의 기본 Record 후보는 다음과 같다.

```text
Task
Assignment
Output
Validation
Audit
```

Review 문서, Disabled Preview, Phase 설명, Readiness 설명마다 독립 Schema를 만들지 않는다.

### Principle 7. Active Architecture Must Be Explainable on One Screen

현재 Active Architecture는 한 화면 안에서 설명할 수 있어야 한다.

```text
Task
 → Core Context
 → Governance Decision
 → Prime Planning / Routing
 → Agent / Program / Tool Work
 → Validation
 → Memory / Audit
```

구조를 설명하기 위해 다수의 Precedence 문서와 중첩된 Review-only Layer를 먼저 이해해야 한다면 과설계 여부를 재검토한다.

---

## 5. Single Authority Map

각 영역은 정확히 하나의 Authority와 하나의 Source of Truth를 가진다.

```yaml
authority_map:

  identity:
    authority: Thomas
    source_of_truth: THOMAS_CORE/MVP_ACTIVE_CORE.yaml

  system_principles:
    authority: Thomas
    source_of_truth: governance/SYSTEM_CONSTITUTION.md

  risk_permission_approval:
    authority: Governance Policy
    source_of_truth: governance/GOVERNANCE_POLICY.yaml

  task_structure:
    authority: Runtime Contract
    source_of_truth: contracts/TASK_CONTRACT.yaml

  role_capability:
    authority: Role Definition
    source_of_truth: role_definition

  role_status:
    authority: Role Registry
    source_of_truth: 03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml

  runtime_execution:
    authority: Runtime Kernel
    source_of_truth: runtime/kernel/

  validation:
    authority: Validation Engine
    source_of_truth: runtime/validation/

  memory:
    authority: Memory Policy
    source_of_truth: runtime/memory/

  audit:
    authority: Audit Contract
    source_of_truth: contracts/AUDIT_CONTRACT.yaml
```

경로는 Step 2~5에서 현재 Repository 구조를 분류한 뒤 최종 확정한다.

핵심 제약:

```text
One Domain
= One Authority
= One Source of Truth
```

---

## 6. 중앙 Governance Rule 모델

Risk, Permission, Approval, Effect 조건은 가능한 한 하나의 Governance Policy 안에서 Rule로 관리한다.

```yaml
rules:

  - id: GOV_001
    condition:
      effect: internal_read
    result: ALLOW

  - id: GOV_002
    condition:
      effect: internal_reversible_write
    result: EXECUTE_AND_REPORT

  - id: GOV_003
    condition:
      effect:
        - external_message
        - public_publish
        - payment
    result: APPROVAL_REQUIRED

  - id: GOV_004
    condition:
      target:
        - thomas_core
        - governance_policy
    result: BLOCK_UNLESS_EXPLICIT_THOMAS_APPROVAL

  - id: GOV_005
    condition:
      authority_expansion: true
    result: BLOCK
```

Role, Assignment, Runtime은 Rule 내용을 복제하지 않고 Rule ID만 참조한다.

---

## 7. Active Architecture 목표 구조

최종 물리 구조는 Step 2~5 검토 후 확정하지만 목표 형태는 다음과 같다.

```text
00_GOVERNANCE/
├─ SYSTEM_CONSTITUTION.md
└─ GOVERNANCE_POLICY.yaml

01_THOMAS_CORE/
├─ MVP_ACTIVE_CORE.yaml
└─ CORE_REFERENCE/

02_RUNTIME_CONTRACTS/
├─ TASK.yaml
├─ ASSIGNMENT.yaml
├─ OUTPUT.yaml
├─ VALIDATION.yaml
└─ AUDIT.yaml

03_CAPABILITIES/
├─ ROLE_REGISTRY.yaml
├─ PROGRAM_REGISTRY.yaml
└─ TOOL_REGISTRY.yaml

runtime/
├─ kernel/
├─ policy/
├─ router/
├─ worker/
├─ validation/
└─ memory/

tests/

deferred/

archive/
```

이 구조는 즉시 이동 명령이 아니다. Step 2 분류, Step 3 Source of Truth 확정, Step 4 Active/Deferred/Historical 분리 후 적용 여부를 결정한다.

---

## 8. 새 Artifact 생성 기준

### New Document

새로운 독립 책임 또는 시스템 경계가 생길 때만 생성한다. 단순 Rule 추가는 기존 Governance Policy 또는 기존 Domain Source에 반영한다.

### New Contract

독립 Lifecycle, 독립 저장/전달 Record, 독립 Runtime 경계가 있을 때만 생성한다.

### New Schema

Runtime Data Boundary일 때만 생성한다.

### New Registry

Runtime이 Component를 동적으로 검색하거나 활성 상태를 판단해야 할 때만 생성한다.

### New Validator

독립 실패 영역이 있을 때만 생성한다. Rule 하나가 추가되었다면 새 Validator 파일 대신 기존 Policy Validator와 공통 Test Harness에 Case를 추가한다.

### New Release Gate

새 Phase마다 Gate를 추가하지 않는다. Active Runtime Gate는 Domain 기준으로 유지한다.

```text
Core
Governance
Task
Capability
Runtime
Validation
Security
```

Deferred Architecture 검증은 Active Release Gate와 분리한다.

---

## 9. Step 2 분류 체계

Repository의 모든 주요 Artifact를 다음 여섯 분류 중 하나로 지정한다.

### ACTIVE_NORMATIVE

현재 시스템의 실제 규칙 또는 Source of Truth.

### ACTIVE_IMPLEMENTATION

현재 Active Architecture를 구현하는 Runtime Code.

### GENERATED

Source of Truth에서 생성되거나 검증 결과로 재생성 가능한 Artifact.

### DEFERRED

미래 Stage에서 필요하지만 현재 Active Runtime 책임이 아닌 설계.

### HISTORICAL

과거 설계, 이전 Version, Review 기록, Reference Snapshot.

### DUPLICATE_CANDIDATE

다른 Source of Truth와 의미가 중복되며 Reference로 축소하거나 통합할 후보.

---

## 10. 변경 통제 원칙

1. 기존 안전 경계를 검토 없이 삭제하지 않는다.
2. 파일 수 감소 자체를 목표로 하지 않는다.
3. Source of Truth 수를 줄이는 것을 우선한다.
4. 중복 내용은 삭제보다 Reference 전환을 우선한다.
5. 미래 설계는 Archive가 아니라 Deferred로 먼저 이동한다.
6. Historical Artifact는 Active Release Gate에서 분리한다.
7. Runtime Behavior 변경과 Architecture Reorganization을 가능한 한 별도 변경 단위로 분리한다.
8. 각 Step은 기존 테스트 결과와 안전 경계를 기록한다.

---

## 11. Stop Rules

```yaml
stop_rules:

  - condition: one_rule_requires_new_contract_schema_validator_fixture_and_gate
    action: review_for_policy_rule_conversion

  - condition: registry_duplicates_definition_content
    action: replace_duplicate_content_with_reference

  - condition: disabled_or_future_components_exceed_active_runtime_components
    action: move_future_components_to_deferred_review

  - condition: active_architecture_cannot_be_explained_on_one_screen
    action: perform_authority_and_boundary_review

  - condition: same_semantic_rule_has_multiple_sources_of_truth
    action: select_one_owner_and_deprecate_duplicates

  - condition: phase_specific_validator_growth_continues
    action: consolidate_into_shared_contract_harness
```

---

## 12. Step 1 Acceptance Criteria

```yaml
step_1_acceptance:
  architecture_baseline_fixed: true
  i0_5_5_frozen: true
  new_governance_expansion_paused: true
  single_source_principle_defined: true
  authority_structure_defined: true
  artifact_creation_rules_defined: true
  step_2_classification_model_defined: true
  runtime_behavior_changed: false
  existing_safety_boundary_removed: false
```

---

## 13. 다음 단계

### Step 2. Repository Artifact Classification

Repository의 주요 파일과 디렉터리를 다음 분류로 매핑한다.

```text
ACTIVE_NORMATIVE
ACTIVE_IMPLEMENTATION
GENERATED
DEFERRED
HISTORICAL
DUPLICATE_CANDIDATE
```

Step 2의 산출물:

1. Repository classification inventory
2. Source-of-truth conflict list
3. Duplicate candidate list
4. Active → Deferred 이동 후보
5. Active → Historical 이동 후보
6. 유지해야 할 안전 경계 목록
7. Step 3 Single Source of Truth 확정 입력 자료

Step 2에서는 대규모 삭제, 이동, 이름 변경을 수행하지 않는다.

---

## 14. 최종 원칙

> 하나의 조건은 하나의 새로운 Contract가 아니다.

> 하나의 Rule은 하나의 Source of Truth에서만 정의한다.

> 다른 문서와 Runtime은 Rule을 복제하지 않고 Reference한다.

> 새로운 파일은 새로운 책임이 생길 때만 만든다.

> 미래 기능은 Requirement로 기록하고 현재 Runtime 구조로 구현하지 않는다.

> Disabled 기능을 증명하기 위한 구조가 Active Capability보다 커지면 개발을 멈추고 재검토한다.

> Thomas Agent는 강한 중심, 명확한 권한, 얇은 Runtime 허리, 확장 가능한 Capability 구조를 유지한다.
