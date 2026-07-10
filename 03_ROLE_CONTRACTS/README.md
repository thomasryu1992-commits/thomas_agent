# Role Contracts

Status: MVP Role Structure v0.2

Owner: Thomas

## 1. Purpose

이 폴더는 Thomas Prime과 Dynamic Role의 역할, 권한, 활성 상태와 Task별 위임 계약을 관리한다.

Prime은 Dynamic Role이 아니며 `MVP_DYNAMIC_ROLE_CONTRACT.md`를 상속하지 않는다.

## 2. Structure

```text
03_ROLE_CONTRACTS/
├── README.md
├── THOMAS_PRIME_CHARTER.md
├── MVP_DYNAMIC_ROLE_CONTRACT.md
├── ROLE_DEFINITION_TEMPLATE.yaml
├── ROLE_ASSIGNMENT_CONTRACT.md
├── ROLE_REGISTRY.yaml
└── ROLES/
    ├── ACTIVE/
    │   ├── GENERAL_SPECIALIST_ROLE.md
    │   └── VALIDATION_ROLE.md
    └── CANDIDATES/
        ├── RESEARCH_ROLE.md
        ├── TRANSLATION_ROLE.md
        ├── CONTENT_ROLE.md
        ├── BUSINESS_ANALYSIS_ROLE.md
        └── DEVELOPMENT_ROLE.md
```

## 3. Document Responsibilities

| Document | Responsibility |
| --- | --- |
| `THOMAS_PRIME_CHARTER.md` | Prime의 역할, 책임, 권한과 금지 경계 |
| `MVP_DYNAMIC_ROLE_CONTRACT.md` | 모든 Dynamic Role이 따라야 하는 공통 규칙 |
| `ROLE_DEFINITION_TEMPLATE.yaml` | Role Definition의 기계 판독 템플릿 |
| `ROLE_ASSIGNMENT_CONTRACT.md` | 특정 Task에서 실제로 부여된 역할, 권한, Context와 예산 |
| `ROLE_REGISTRY.yaml` | 현재 등록된 역할, 버전, 상태와 라우팅 가능 여부 |
| `ROLES/ACTIVE/` | Prime이 현재 자동 선택할 수 있는 역할 |
| `ROLES/CANDIDATES/` | 검토와 시험은 가능하지만 자동 선택할 수 없는 역할 |

## 4. Core Concept Differences

Role 관련 문서는 다음 순서로 구체화된다.

```text
MVP_DYNAMIC_ROLE_CONTRACT.md
모든 Dynamic Role의 공통 규칙

↓

ROLE_DEFINITION_TEMPLATE.yaml
개별 Role Definition의 필드 구조

↓

ROLE_REGISTRY.yaml
현재 등록된 Role의 버전, 상태와 라우팅 가능 여부

↓

ROLE_ASSIGNMENT_CONTRACT.md
특정 Task에서 실제로 허용된 Core, 입력, 권한, Tool과 예산

↓

Agent Output v0.1
Role 실행 결과
```

### 4.1 Role Definition

Role Definition은 역할이 일반적으로 무엇을 할 수 있는지 정의한다.

다음을 포함한다.

- 역할 목적과 Capability
- 활성화 및 비활성화 조건
- Permission Ceiling
- 사용 가능한 Program과 Tool의 상한
- Memory와 Validation 정책
- 역할별 Budget Cap
- 중지, 완료와 품질 기준

Role Definition은 특정 Task의 실제 실행 권한을 부여하지 않는다.

### 4.2 Role Registry

Role Registry는 Role Router가 사용하는 현재 상태의 단일 기준이다.

다음을 결정한다.

- 어떤 Role이 등록되어 있는가
- 어떤 Version이 현재 기준인가
- 상태가 `active`, `candidate`, `disabled` 중 무엇인가
- Prime이 자동 선택할 수 있는가
- Role Definition이 어디에 있는가

폴더에 Role 파일이 있어도 Registry에서 `active` 및 `routable: true`가 아니면 자동 실행할 수 없다.

### 4.3 Role Assignment

Role Assignment는 특정 Task에서 실제로 부여된 실행 범위다.

다음을 포함한다.

- Task ID와 고정된 Role Version
- Task에 필요한 Active Core Rule ID
- 허용된 입력과 Context Reference
- 실제 사용 가능한 Program과 Tool
- Assignment에 부여된 Permission
- Validation 수준
- 숫자로 지정된 Token, 비용, 시간과 호출 한도
- Assignment 만료 시각과 에스컬레이션 대상

Role Definition만으로 Dynamic Role을 실행할 수 없다.

유효한 Task와 Role Assignment가 모두 있어야 한다.

## 5. Role Status

Role의 주요 운영 상태는 다음과 같다.

### `active`

- 승인된 Role Definition과 Version이 있다.
- Registry에서 `routable: true`로 지정할 수 있다.
- Prime이 조건에 맞는 Task에서 자동 선택할 수 있다.

### `candidate`

- Role Definition 초안과 제한 시험은 가능하다.
- Registry에 등록할 수 있지만 `routable: false`다.
- Prime은 일반 Task에 자동 배정할 수 없다.
- 활성화 시험에는 Thomas 승인 또는 명시된 시험 Assignment가 필요하다.

### `disabled`

- Registry 기록과 Version은 유지한다.
- 안전, 비용, Tool, 품질 또는 운영 문제로 사용을 일시 중지한다.
- 신규 Assignment를 만들 수 없다.
- 원인이 해결되고 Thomas가 재활성화를 승인해야 `active`로 돌아갈 수 있다.

보조 상태는 다음과 같다.

- `draft`: 아직 검토와 Registry 등록이 완료되지 않음
- `deprecated`: 대체 Role이 있어 신규 Assignment를 만들지 않음
- `archived`: 과거 Task와 Audit 호환을 위해서만 보존

## 6. MVP Active Roles

MVP 자동 라우팅 대상은 다음 두 역할로 제한한다.

- `general.specialist`: 조사, 분석, 기획, 작성 등 낮은 위험의 일반 전문 판단
- `validation.independent`: 생성 결과의 독립 검토

Research, Translation, Content, Business Analysis, Development는 후보 역할이다.

후보 역할은 다음 조건이 확인될 때 활성화할 수 있다.

- 동일한 전문 업무가 반복된다.
- General Specialist와 다른 전문 기준이 필요하다.
- 별도 Tool, Memory 또는 권한 경계가 필요하다.
- 분리했을 때 품질, 비용 또는 검증 가능성이 실제로 개선된다.
- Thomas가 `ROLE_REGISTRY.yaml`의 활성화를 승인한다.

## 7. Candidate to Active Promotion

Candidate Role은 다음 절차를 거쳐 활성화한다.

```text
반복 필요 확인
↓
Candidate Role Definition 작성 또는 수정
↓
권한, Tool, Memory, Validation과 Budget 검토
↓
제한된 Task 시험
↓
품질, 비용, 실패율과 General Specialist 대비 차이 평가
↓
Thomas 승인
↓
Role Version 확정
↓
ROLE_REGISTRY를 active 및 routable로 변경
↓
변경 기록과 Audit 저장
```

승격 조건은 다음과 같다.

- 완료된 유사 Task 3건 이상에서 반복 필요가 확인된다.
- General Specialist와 다른 전문 기준 또는 Capability가 필요하다.
- 별도 Role이 품질, 비용, 속도, 권한 격리 또는 검증 가능성을 개선한다.
- Permission Ceiling, Program, Tool, Memory, Validation과 Budget Cap이 정의되어 있다.
- 제한 시험에서 중대한 권한 위반이나 안전 문제가 없다.
- 기존 Task와 Agent Output 계약을 준수한다.
- Thomas가 정확한 Role Version의 활성화를 승인한다.

승격 기준을 충족하지 않으면 General Specialist의 Task별 전문 지시로 유지한다.

## 8. Role Selection Flow

Prime은 다음 순서로 Role을 사용한다.

```text
Task v0.2 접수
↓
Prime이 실행 방식, 복잡도, 위험과 Permission을 분류
↓
Program만으로 충분한지 확인
├─ YES → Program 실행
└─ NO
    ↓
ROLE_REGISTRY에서 active + routable Role 검색
↓
필요 Capability를 충족하는 최소 Role 선택
↓
Policy, Permission, Tool과 Budget 검사
↓
Role Assignment 발급
↓
Role 실행
↓
Agent Output v0.1
↓
필요 시 Independent Validation
↓
Prime 결과 통합
```

Role 선택 원칙은 다음과 같다.

1. Task에 필요한 Capability를 모두 충족한다.
2. 필요한 최소 Permission으로 수행할 수 있다.
3. 충분한 역할 중 더 좁은 범위와 낮은 비용을 우선한다.
4. 전문성 차이가 실제 품질에 영향을 줄 때만 전문 Role을 선택한다.
5. Candidate, disabled, deprecated Role은 자동 선택하지 않는다.

폴더에 파일이 존재한다는 이유만으로 역할을 활성화하지 않는다.

## 9. Permission Ceiling Rule

다음 관계는 개념적인 상한을 나타낸다.

```text
System Policy Maximum
≥ Role Permission Ceiling
≥ Assignment Granted Permission
≥ Actual Runtime Permission
```

실제 권한은 단순한 숫자 비교가 아니라 모든 허용 범위의 교집합으로 결정한다.

```text
Actual Runtime Permission =
intersection(
  System Policy Decision,
  Task Permission,
  Role Permission Ceiling,
  Assignment Granted Permission,
  Tool or Program Scope
)
```

권한 규칙은 다음과 같다.

- 어느 하나라도 행동을 허용하지 않으면 실행할 수 없다.
- Assignment는 Role Permission Ceiling보다 높은 권한을 부여할 수 없다.
- Policy Engine은 Role 또는 Assignment의 권한을 낮추거나 차단할 수 있다.
- Tool과 Program은 Role의 권한을 확대할 수 없다.
- Runtime은 판단 과정에서 권한을 자동으로 상향할 수 없다.
- 새로운 권한이 필요하면 실행을 중지하고 새 Permission Decision과 Role Assignment를 요청한다.

## 10. Document Precedence

문서와 실행 지시가 충돌하면 다음 우선순위를 적용한다.

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

Thomas의 일반 Task 지시는 Active Core, 금지 행동, Permission, Risk와 실행 한도를 암묵적으로 변경하지 않는다.

Core, Policy 또는 Role 권한을 변경하려면 해당 문서의 명시적 변경 절차를 사용한다.

비활성 문서는 Runtime 실행 근거로 사용하지 않는다.

안전하게 해결할 수 없는 충돌은 실행하지 않고 Thomas Prime에게 전달한다.

## 11. Runtime Read Order

Runtime 읽기 순서는 시스템 시작과 Task 실행을 구분한다.

### 11.1 System Startup

```text
CORE_METADATA.yaml
↓
MVP_ACTIVE_CORE.yaml
↓
MVP Operating Policy
↓
Common I/O Contracts
↓
Thomas Prime Charter
↓
MVP Dynamic Role Contract
↓
ROLE_REGISTRY.yaml
```

시스템 시작 시 Candidate Role의 전체 내용을 모두 로드할 필요는 없다.

Registry의 상태와 경로만 확인한다.

### 11.2 Task Routing

```text
Task v0.2
↓
ROLE_REGISTRY에서 Role 후보 검색
↓
선택된 Role Definition과 Version 확인
↓
Policy, Permission과 Budget 검사
↓
Role Assignment 생성
```

### 11.3 Role Execution

```text
Role Assignment
↓
Assignment에 고정된 Role Definition Version
↓
Task Input Reference
↓
Assignment에 지정된 Active Core와 Context Reference
↓
허용된 Program, Tool과 Budget
↓
Agent Output v0.1
```

Role Runtime은 Assignment에 포함되지 않은 문서, Memory, Tool, Program과 권한을 임의로 추가 로드하지 않는다.

## 12. Version and Change Policy

Role Definition은 Semantic Versioning을 사용한다.

```text
MAJOR.MINOR.PATCH
```

- `MAJOR`: 권한, 입력·출력 계약, Capability 또는 호환성을 깨는 변경
- `MINOR`: 호환 가능한 Capability, 품질 기준 또는 선택 조건 추가
- `PATCH`: 의미를 바꾸지 않는 설명, 오탈자와 낮은 위험 수정

변경 규칙은 다음과 같다.

- Registry는 현재 승인된 정확한 Role Version을 기록한다.
- Role Assignment는 실행 시점의 Role Version을 고정한다.
- Role Definition 변경은 실행 중인 Assignment에 소급 적용하지 않는다.
- 실행 중인 Assignment의 Role Version을 바꾸려면 기존 Assignment를 무효화하고 새로 발급한다.
- Candidate 활성화, Active 비활성화와 Disabled 재활성화는 Thomas 승인을 요구한다.
- Permission Ceiling과 공통 라우팅 변경은 Thomas 승인과 Audit 기록을 요구한다.
- Agent와 Role은 자신의 Definition, Version, 상태와 권한을 직접 변경할 수 없다.
- 과거 Version은 기존 Task와 Audit 재현을 위해 삭제하지 않는다.

## 13. Official Runtime Summary

```text
Role Definition
→ 역할의 지속적인 능력과 절대 상한

Role Registry
→ 현재 사용할 수 있는 역할과 Version

Role Assignment
→ 이번 Task에서 실제로 허용된 범위

Agent Output
→ 실행 결과
```

> Runtime은 Registry에서 활성화된 Role만 선택하고, Role Assignment에 명시된 범위 안에서만 실행한다.

> 실제 권한은 Policy, Task, Role, Assignment, Tool과 Program Scope의 교집합을 초과할 수 없다.
