# Role Contracts

Status: MVP Role Structure v0.1

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

## 4. Runtime Flow

```text
Task v0.2
↓
Thomas Prime
↓
ROLE_REGISTRY에서 활성 Role 선택
↓
Role Assignment 발급
↓
Role 실행
↓
Agent Output v0.1
↓
Validation and Result Integration
```

Role Definition은 역할이 일반적으로 무엇을 할 수 있는지 정의한다.

Role Assignment는 이번 Task에서 실제로 무엇을 할 수 있는지 정의한다.

Role Definition만으로 Dynamic Role을 실행할 수 없다.

## 5. MVP Active Roles

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

## 6. Loading Rule

Prime은 다음 순서로 Role을 사용한다.

1. `ROLE_REGISTRY.yaml`에서 `active`이면서 `routable: true`인 역할만 검색한다.
2. Task에 충분한 최소 Capability와 Permission을 가진 역할을 선택한다.
3. `ROLE_ASSIGNMENT_CONTRACT.md`에 따라 Task별 Assignment를 만든다.
4. Assignment에 포함되지 않은 Core, Memory, Tool, Program과 예산은 사용할 수 없다.

폴더에 파일이 존재한다는 이유만으로 역할을 활성화하지 않는다.
