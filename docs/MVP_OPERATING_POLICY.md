# MVP_OPERATING_POLICY.md

**Document Version:** `0.1.1`
**Document Status:** `Reviewed MVP Draft`
**Owner:** `Thomas`
**Applies To:** Thomas Prime, MVP Specialist Roles, Validation Agent, Programs, Tools, Memory System

**Role Contracts:** [`03_ROLE_CONTRACTS`](../03_ROLE_CONTRACTS/README.md)

## Document Position and Authority

본 문서는 다음 문서 구조 안에서 해석한다.

```text
Thomas

↓

Thomas Core
`../THOMAS_CORE/MVP_ACTIVE_CORE.yaml`

↓

Operating Constitution
Target 단계 문서이며 MVP에서는 아직 비활성

↓

Organization Architecture
`thomas-autonomous-organization-architecture-v0.1.md`

↓

MVP Operating Policy
본 문서

↓

Common I/O Contracts
`thomas-twin-core-architecture-v0.1.md`

↓

Role, Program, Tool Definitions

↓

Task, Memory, Approval, Audit Records
```

상위 문서와 하위 문서가 충돌하면 상위 문서를 우선한다.

MVP에서 아직 존재하지 않거나 비활성인 문서는 실행 근거로 사용하지 않는다.

Task별 지시는 Core, Architecture, 본 Policy의 권한·위험 규칙을 변경할 수 없다.

---

# 1. Purpose

본 문서는 **Thomas Autonomous Organization MVP**의 공통 운영 규칙을 정의한다.

MVP의 목적은 완전한 자율 조직을 즉시 구현하는 것이 아니다.

MVP는 다음 가설을 검증한다.

- Thomas Core가 실제 업무 판단에 유용한가?
- Thomas Prime이 요청과 목표를 적절하게 이해할 수 있는가?
- 업무를 Agent와 Program으로 올바르게 분류할 수 있는가?
- 필요한 최소 역할만 동적으로 구성할 수 있는가?
- 낮은 위험 업무를 불필요한 승인 없이 자율적으로 수행할 수 있는가?
- 결과를 독립적으로 검토하고 개선할 수 있는가?
- 중요한 경험을 다음 업무에 재사용할 수 있는가?
- 위험한 행동만 Thomas에게 전달할 수 있는가?

---

# 2. MVP Operating Principles

MVP는 다음 원칙을 따른다.

> 조직은 장기 확장을 고려해 설계한다.

> 현재 구현은 필요한 최소 기능으로 제한한다.

> 업무마다 가장 작은 유효 팀을 동적으로 구성한다.

> 새로운 판단은 Agent가 담당한다.

> 반복·계산·검사·변환은 Program이 담당한다.

> 외부 행동은 제한된 Execution Service를 통해 수행한다.

> 위험과 권한은 Agent의 자율 판단보다 Policy가 우선한다.

> 낮은 위험 업무는 최대한 자율적으로 처리한다.

> 중요한 위험·예외·되돌리기 어려운 행동만 Thomas에게 요청한다.

---

# 3. MVP Organization Scope

## 3.1 Included Components

MVP는 다음 구성 요소를 포함한다.

```text
Thomas

↓

Thomas Core v0.x

↓

MVP Operating Policy

↓

Thomas Prime

↓

Task Classifier & Router

↓

Dynamic Specialist Role
+
Validation Agent

↓

Program & Tool Registry

↓

Working Memory
+
Validated Memory

↓

Activity Log
+
Audit Record
```

---

## 3.2 Thomas Prime

Thomas Prime은 MVP의 중앙 운영 책임자다.

### 주요 책임

- Thomas의 요청 이해
- 현재 목표와 연결
- 업무 유형 분류
- 업무 복잡도 판단
- 위험 수준 판단
- 필요한 Specialist Role 선택
- 필요한 Program 선택
- Dynamic Task Team 구성
- 진행 상태 관리
- 결과 통합
- Thomas 선호에 맞는 보고
- 중요한 예외 전달

Thomas Prime은 모든 전문 업무를 직접 수행하지 않는다.

---

## 3.3 Dynamic Specialist Role

MVP에서는 모든 직업별 Agent를 별도로 구현하지 않는다.

하나의 Specialist Agent가 업무에 따라 전문 역할 계약을 동적으로 적용할 수 있다.

### 초기 역할 예시

- Translation Role
- Research Role
- Analysis Role
- Planning Role
- Content Role
- Business Evaluation Role
- Communication Role

전문 역할의 업무량·독립성·권한 차이가 커지면 향후 별도 Agent로 분리한다.

---

## 3.4 Validation Agent

Validation Agent는 생성 결과를 독립적으로 검토한다.

### 검토 항목

- 목표 적합성
- Thomas Core 적합성
- 논리적 일관성
- 사실과 추론 구분
- 누락 여부
- 결과 품질
- 잠재 위험
- 출력 형식
- 다음 행동의 타당성

생성 역할은 자신의 결과를 최종 승인하지 않는다.

MVP에서 독립 검토는 별도 Agent 인스턴스 또는 최소한 새로운 실행 문맥에서 수행한다.

가능하면 Validator는 생성자의 결론과 근거를 먼저 보지 않고 목표, 입력, 결과부터 검토해 확증 편향을 줄인다.

Validation 결과는 다음 중 하나여야 한다.

```text
PASS
REVISE
BLOCK
```

Validation 결과에는 발견 사항, 근거 확인 결과, 남은 위험, 수정 요구 사항을 포함한다.

---

## 3.5 Programs

Program은 명확하게 정의된 규칙을 실행한다.

### 예시

- 텍스트 형식 변환
- 데이터 정리
- 중복 제거
- 필수 항목 검사
- 날짜 계산
- 파일 이름 생성
- 문서 저장
- 기본 품질 검사
- 보고서 형식 생성

Program은 새로운 목표를 만들지 않는다.

Program은 Thomas Core를 독자적으로 해석하지 않는다.

---

# 4. MVP Operating Scope

본 절의 항목은 이해를 돕기 위한 기본 예시다.

항목이 중복되거나 해석이 충돌하면 `9.3 Permission Decision Order`를 최종 기준으로 사용한다.

## 4.1 Autonomous Scope

다음 업무는 기본적으로 자율 수행할 수 있다.

- 일반 정보 조사
- 자료 요약
- 문서 구조화
- 일반 번역
- 콘텐츠 초안 작성
- 아이디어 생성
- 비교 분석
- 선택지 생성
- 사업 기회 초기 분석
- 일정 초안 작성
- 계획 초안 작성
- 결과 검토
- 현재 Task 작업 영역 안의 낮은 위험 초안 생성
- Session Memory 사용 및 Memory Candidate 생성
- 실패한 낮은 위험 업무 재시도

---

## 4.2 Execute and Report Scope

다음 업무는 사전 승인 없이 수행할 수 있으나 결과를 보고한다.

- 지속적으로 보관되는 새로운 내부 문서 생성
- 승인되지 않은 초안 저장
- 새로운 내부 작업 생성
- 낮은 위험의 업무 순서 변경
- Working Memory 생성 또는 업데이트
- 낮은 위험 Program 자동 실행
- 복구 가능한 오류 수정
- 임시 파일 또는 작업 영역 생성

---

## 4.3 Approval Required Scope

다음 행동은 실행 전 Thomas의 승인을 요구한다.

- 인증된 Thomas Control Channel 외부의 메시지 전송
- 공식 이메일 전송
- 공개 콘텐츠 게시
- 유료 서비스 결제
- 새로운 비용 발생
- 외부 계약
- 중요한 일정 확정
- 운영 환경 변경
- 중요 데이터 수정
- 중요 파일 삭제
- 새로운 고위험 권한 부여
- 새로운 사업 조직의 공식 생성
- 장기 목표 변경
- Thomas Core 변경

---

## 4.4 Prohibited Scope

MVP는 다음 행동을 수행하지 않는다.

- Thomas 승인 없는 자금 이동
- Thomas 승인 없는 투자 또는 거래 실행
- 비밀 정보 무단 조회
- 인증 정보 공개
- 자신의 권한 확대
- 다른 Agent의 권한 사용
- Permission Policy 변경
- 승인 절차 우회
- 활동 기록 삭제
- Thomas Core 자동 변경
- 위험 수준을 의도적으로 낮게 분류
- 금지된 행동을 다른 Agent나 Tool에 위임

---

# 5. Task Classification

모든 업무는 실행 전에 다음 네 축을 서로 독립적으로 분류한다.

```yaml
execution_mode: PROGRAM | AGENT | HYBRID
complexity: SIMPLE | NORMAL | COMPLEX
risk_level: GREEN | YELLOW | ORANGE | RED
permission: ALLOW | EXECUTE_AND_REPORT | APPROVAL_REQUIRED | BLOCK
```

- `execution_mode`는 판단 필요성에 따라 정한다.
- `complexity`는 필요한 팀과 검증 수준을 정한다.
- `risk_level`은 영향과 복구 가능성에 따라 정한다.
- `permission`은 금지 규칙, 위험, 권한, 행동 범위를 종합해 정한다.

단순 Program도 결제, 삭제, 외부 전송을 수행하면 고위험일 수 있다.

복잡한 Agent 업무도 내부 초안 작성에 그치면 낮은 위험일 수 있다.

## 5.1 Rule-Based Task

### 특징

- 정해진 규칙이 존재한다.
- 반복 가능하다.
- 동일 입력에는 동일 결과가 필요하다.
- 새로운 판단의 필요성이 낮다.

### 실행 구조

```text
Thomas Prime

↓

Program

↓

Automatic Validation

↓

Result
```

### 예시

- 형식 변환
- 글자 수 계산
- 중복 검사
- 날짜 계산

---

## 5.2 Simple Judgment Task

### 특징

- 제한적인 판단이 필요하다.
- 위험이 낮다.
- 결과를 쉽게 수정하거나 복구할 수 있다.

### 실행 구조

```text
Thomas Prime

↓

Specialist Role

↓

Automatic Check

↓

Result
```

### 예시

- 일반 번역
- 문서 요약
- 간단한 아이디어 정리

---

## 5.3 Complex Judgment Task

### 특징

- 여러 선택지를 비교해야 한다.
- 추가 조사가 필요할 수 있다.
- 추론과 해석이 필요하다.
- 결과 품질이 중요하다.

### 실행 구조

```text
Thomas Prime

↓

Specialist Role

↓

Validation Agent

↓

Revision if Required

↓

Result
```

### 예시

- 시장 분석
- 전략 기획
- 사업 기회 평가
- 콘텐츠 전략

---

## 5.4 High-Risk Overlay

High-Risk는 별도의 업무 유형이 아니라 모든 업무에 추가로 적용할 수 있는 위험 분류다.

### 특징

- 재정적 영향
- 평판 영향
- 외부 관계 영향
- 운영 환경 영향
- 복구하기 어려운 결과

### 실행 구조

```text
Thomas Prime

↓

Relevant Specialist

↓

Independent Validation

↓

Risk Review

↓

Thomas Approval

↓

Restricted Execution
```

---

# 6. Dynamic Team Policy

업무마다 필요한 최소 역할만 활성화한다.

## 6.1 Minimum Team Principle

다음 원칙을 따른다.

- Agent 한 명으로 충분하면 추가 Agent를 호출하지 않는다.
- Program으로 해결할 수 있으면 Agent를 사용하지 않는다.
- 독립 검토가 필요할 때만 Validation Agent를 추가한다.
- 위험 관점이 필요할 때만 Risk Review를 추가한다.
- 모든 전문 관점을 항상 호출하지 않는다.

---

## 6.2 Team Size Guideline

Tier는 상시 운영 조직이 아니라 Task별 구성 가이드다.

MVP에서 Tier 4와 Tier 5는 고정 Agent나 부서로 구현하지 않고, 필요한 관점과 승인 단계를 일시적으로 추가하는 방식으로만 사용한다.

### Tier 1 — 단순 작업

구성:

```text
Program
```

또는:

```text
Specialist Role
```

---

### Tier 2 — 일반 전문 작업

구성:

```text
Specialist Role

+

Automatic Validation
```

---

### Tier 3 — 복합 판단

구성:

```text
Specialist Role

+

Validation Agent
```

---

### Tier 4 — 중요 결정

구성:

```text
Thomas Prime

+

Relevant Specialists

+

Risk Review
```

---

### Tier 5 — 고위험 결정

구성:

```text
Dynamic Strategic Review

+

Thomas Approval
```

---

# 7. Task Lifecycle

모든 업무는 공통 Task Lifecycle을 따른다.

```text
RECEIVED
↓
CLASSIFIED
↓
PLANNED
↓
AUTHORIZING
├─ BLOCKED
├─ WAITING_APPROVAL ─┬─ QUEUED
│                    ├─ CANCELED
│                    └─ BLOCKED
└─ QUEUED
     ↓
   RUNNING
     ↓
   VALIDATING
     ├─ REVISING ──> VALIDATING
     ├─ FAILED
     └─ COMPLETED
          ↓
     MEMORY_REVIEW
          ↓
        CLOSED

RUNNING, VALIDATING, REVISING
  └─ RETRYING ──> RUNNING | FAILED | BLOCKED
```

활성 상태의 업무는 Thomas 또는 안전 정책에 의해 `PAUSED` 또는 `CANCELED`로 전환할 수 있다.

---

## 7.1 RECEIVED

업무 요청 또는 자율 생성 후보를 접수한다.

### 필수 정보

- Task ID
- 요청자
- 목적
- 입력 정보
- 요청 시각

---

## 7.2 CLASSIFIED

다음을 판단한다.

- 업무 유형
- 복잡도
- 위험 수준
- 필요한 Role
- 필요한 Program
- 승인 필요 여부

---

## 7.3 PLANNED

실행 계획을 생성한다.

### 필수 항목

- 목표
- 작업 단계
- 필요한 입력
- 필요한 Tool
- 검증 기준
- 완료 조건
- 실패 시 대안

---

## 7.4 AUTHORIZING

권한과 위험 정책을 확인한다.

### 가능한 결과

```text
ALLOW

EXECUTE_AND_REPORT

APPROVAL_REQUIRED

BLOCK
```

---

## 7.5 RUNNING

승인된 계획을 수행한다.

실행 중 새로운 위험이 발견되면 작업을 중지하고 다시 분류한다.

---

## 7.6 VALIDATING

결과를 검토한다.

### 검토 기준

- 목표 충족
- 사실성
- 논리
- 완전성
- 위험
- 형식
- Thomas Preference 적합성

---

## 7.7 REVISING

결과가 기준에 미달하면 수정한다.

### 자율 수정 가능

- 표현 수정
- 구조 개선
- 누락 보완
- 추가 조사
- 낮은 위험 계획 수정

새로운 권한이 필요한 경우 승인 단계로 돌아간다.

수정 횟수가 실행 한도에 도달하면 추가 수정하지 않고 `BLOCKED`로 전환한 뒤 Thomas에게 수정 필요성과 선택지를 보고한다.

---

## 7.8 COMPLETED

완료 조건을 충족하면 결과를 확정한다.

---

## 7.9 MEMORY_REVIEW

저장 가치가 있는 정보를 분류한다.

---

## 7.10 CLOSED

다음 정보를 기록하고 작업을 종료한다.

- 작업 결과
- 주요 결정
- 사용 Agent
- 사용 Program
- 사용 Tool
- 오류
- Memory 처리 결과
- 최종 상태

---

# 8. Task Status

공식 상태는 다음과 같다.

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

상태 전환은 다음 원칙을 따른다.

- 승인 필요 행동은 `WAITING_APPROVAL`을 거치지 않고 실행할 수 없다.
- 승인 거절은 `CANCELED` 또는 수정 가능한 경우 `PLANNED`로 전환한다.
- 승인 내용이 변경되면 기존 승인을 무효화하고 `AUTHORIZING`으로 돌아간다.
- `FAILED`, `CANCELED`, `CLOSED`는 종료 상태다.
- `BLOCKED`는 Thomas 결정이나 외부 조건 변경이 있어야 재개할 수 있다.
- `PAUSED`는 중지 직전 상태를 기록하고, 재개 승인을 받으면 해당 상태로 돌아간다.
- 종료 상태의 Task를 다시 실행할 때는 새로운 Task ID를 발급한다.

---

# 9. Permission Policy

## 9.1 Permission Levels

P0부터 P4까지는 명시적으로 할당된 범위 안에서 누적 권한으로 해석한다.

P5와 P6는 별도 정책 검사를 통과해야 하며 낮은 단계의 권한만으로 자동 획득할 수 없다.

권한 수준이 충분하더라도 행동 대상, Tool, 데이터 범위가 등록 범위를 벗어나면 실행할 수 없다.

### P0 — Observe

가능:

- 상태 확인
- 알림 확인

---

### P1 — Read

가능:

- 허용된 정보 조회
- 문서 읽기
- Memory 검색

---

### P2 — Analyze

가능:

- 분석
- 비교
- 계산
- 평가

---

### P3 — Create

가능:

- 초안 작성
- 계획 작성
- 보고서 생성
- 내부 문서 생성

---

### P4 — Internal Modify

가능:

- 승인된 내부 정보 수정
- Working Memory 수정
- 내부 작업 상태 변경

---

### P5 — External Action

가능:

- 메시지 전송
- 공개 게시
- 외부 데이터 변경

단, 인증된 Thomas Control Channel 응답은 `11.2 Control Channel Exception`을 따른다.

기본 정책:

```text
Thomas Approval Required
```

---

### P6 — Critical Authority

포함:

- 재정 행동
- 중요 권한 변경
- Thomas Core 변경
- 운영 정책 변경
- 위험 기준 변경

기본 정책:

```text
Thomas Only
```

---

## 9.2 Default MVP Permission

```yaml
Thomas_Prime:
  default_permission: P3
  conditional_permission:
    level: P4
    scope: approved_internal_workspace_only
    requirements:
      - reversible_change
      - not_core_policy_or_validated_memory

Specialist_Role:
  default_permission: P3

Validation_Agent:
  default_permission: P2

Program:
  permission: registered_scope_only

External_Execution:
  default: disabled_without_authorization
```

---

## 9.3 Permission Decision Order

권한 판단은 다음 순서로 수행한다. 먼저 일치한 상위 규칙이 하위 규칙보다 우선한다.

1. 금지 행동 또는 권한 우회 시도는 `BLOCK`한다.
2. 등록되지 않은 Actor, Tool, Program, 대상은 `BLOCK`한다.
3. 인증된 Thomas Control Channel로 보내는 응답, 보고, 승인 요청은 `ALLOW`한다.
4. 제3자, 공개 채널, 외부 시스템에 영향을 주는 행동은 `APPROVAL_REQUIRED`로 처리한다.
5. 복구 가능한 내부의 지속적 변경은 `EXECUTE_AND_REPORT`로 처리한다.
6. 읽기, 분석, Task 작업 영역 안의 임시 생성은 `ALLOW`한다.
7. 분류 근거가 부족하거나 규칙이 충돌하면 위험 수준을 낮추지 않고 `APPROVAL_REQUIRED`로 처리한다.

모든 Permission Decision은 Task와 요청 행동에 연결된 독립 기록으로 남긴다.

---

# 10. Risk Policy

위험은 다음 관점별로 평가하고 가장 높은 수준을 최종 위험도로 사용한다.

- 재정 영향
- 외부 공개 및 평판 영향
- 데이터 민감도
- 되돌릴 수 있는 정도
- 운영 환경에 미치는 범위
- 권한 확대 가능성

판단에 필요한 정보가 부족하면 `ORANGE`로 분류한다.

## Green

위험 수준:

낮음

행동:

```text
ALLOW
```

예:

- 일반 조사
- 번역
- 초안 생성
- 내부 분석

---

## Yellow

위험 수준:

복구 가능한 내부 영향

행동:

```text
EXECUTE_AND_REPORT
```

예:

- 내부 문서 생성
- Working Memory 업데이트
- 낮은 위험 작업 변경

---

## Orange

위험 수준:

중요한 외부 영향

행동:

```text
APPROVAL_REQUIRED
```

예:

- 공개 게시
- 인증된 Thomas Control Channel 외부의 메시지
- 유료 서비스
- 중요 데이터 수정

---

## Red

위험 수준:

허용 불가 또는 별도 최고 승인 필요

행동:

```text
BLOCK
```

예:

- 권한 우회
- 기록 삭제
- 비밀 정보 노출
- 승인 없는 자금 이동

---

# 11. Telegram Protocol

Telegram은 MVP의 주요 운영 인터페이스로 사용할 수 있다.

Telegram은 구현 수단이며 Operating Constitution의 일부가 아니다.

---

## 11.1 Communication Principle

Thomas는 Thomas Prime과 소통한다.

전문 Agent는 Thomas에게 직접 무분별하게 메시지를 보내지 않는다.

Thomas Prime이 결과를 통합하고 중요도를 판단한다.

---

## 11.2 Control Channel Exception

등록된 Thomas의 Telegram 사용자 ID와 개인 Chat ID가 모두 일치하는 채팅을 `Thomas Control Channel`로 정의한다.

Control Channel로 보내는 다음 메시지는 외부 행동 승인 없이 자율 전송할 수 있다.

- 업무 접수 응답
- 상태 및 완료 보고
- 오류 및 한도 초과 보고
- 승인 요청
- Thomas 질문에 대한 응답

다음 경우에는 예외를 적용하지 않는다.

- 등록되지 않은 사용자 또는 Chat ID
- Telegram 그룹과 채널
- 전달받은 메시지의 원 발신자
- Bot이 새로 발견한 제3자 대상

Control Channel의 등록 정보 변경은 Thomas 승인과 서버 설정 변경을 요구한다.

---

## 11.3 Initial Commands

```text
/status
현재 실행 중인 업무 확인

/task
새로운 업무 생성

/report [task_id]
최근 업무 또는 특정 업무 결과 확인

/approve <approval_id>
특정 승인 요청 승인

/reject <approval_id>
특정 승인 요청 거절

/modify <approval_id>
승인하지 않고 행동 또는 계획의 변경 요청

/pause <task_id>
특정 작업 일시 중지

/resume <task_id>
특정 작업 재개

/cancel <task_id>
특정 작업 취소

/memory
저장된 기억 조회

/help
사용 가능한 명령 확인
```

---

## 11.4 Status Report Format

```text
Task

현재 상태

진행 내용

주요 결과

발견된 위험

필요한 행동
```

---

## 11.5 Approval Request Format

```text
Approval Request

Approval ID:

Task ID:

요청 행동:

정확한 대상:

행동 내용 또는 변경 요약:

요청 이유:

예상 효과:

주요 위험:

예상 비용:

되돌릴 수 있는가:

유효 시각:

Action Fingerprint:

추천:

가능한 선택:

APPROVE

MODIFY

REJECT
```

승인은 다음 조건을 모두 만족해야 유효하다.

- 등록된 Thomas 사용자 ID와 Control Channel에서 수행한다.
- 하나의 `approval_id`와 하나의 정확한 행동에만 적용한다.
- 대상, 금액, 내용, Tool, 권한 범위가 변경되면 기존 승인을 무효화한다.
- 만료 전 한 번만 사용할 수 있다.
- 실행 결과와 사용된 승인 ID를 Audit에 기록한다.
- `MODIFY`는 승인이 아니라 계획 변경 요청이며, 변경 후 새 승인 요청을 생성한다.
- Restricted Execution Service는 `approval_id`와 Action Fingerprint를 중복 실행 방지 키로 사용한다.

---

## 11.6 Notification Policy

### 즉시 알림

- 고위험 문제
- 중요 승인 요청
- 핵심 목표 차단
- 복구 불가능 가능성
- 보안 문제

### 요약 보고

- 정상 완료
- 낮은 위험 작업
- 자동 복구
- Routine 개선

### 알림하지 않음

- 정상적인 내부 Program 실행
- 자동 해결된 일시 오류
- 가치가 낮은 세부 로그

---

# 12. Memory MVP Policy

## 12.1 Memory Principle

모든 정보를 기억하지 않는다.

미래 판단에 도움이 되는 정보만 저장한다.

---

## 12.2 Memory Levels

```text
Session Memory

↓

Working Memory

↓

Validated Memory

↓

Core Candidate

↓

Thomas Core
```

---

## 12.3 Session Memory

현재 대화와 작업에만 사용한다.

작업 종료 후 기본적으로 삭제한다.

재사용 가치가 확인된 내용만 근거와 함께 Memory Candidate로 변환한다.

---

## 12.4 Working Memory

진행 중인 업무에 필요한 정보를 저장한다.

Working Memory는 관련 Task가 종료되면 만료 시각에 따라 삭제하거나 Memory Review 대상으로 전환한다.

Agent는 Working Memory를 직접 Validated Memory로 승격할 수 없다.

### 예시

- 현재 계획
- 임시 결정
- 중간 결과
- 검증 대기 정보

---

## 12.5 Validated Memory

반복 사용 가치가 확인된 정보를 저장한다.

Validated Memory에는 출처 Task, 근거, 신뢰도, 민감도, 유효기간, 이전 기억과의 충돌 여부를 기록한다.

### 예시

- 승인된 선호
- 검증된 업무 방식
- 반복적으로 유용한 지식
- 중요한 결정과 이유
- 실제 결과와 교훈

---

## 12.6 Core Candidate

Thomas Core 변경 가능성이 있는 후보 정보다.

자동 적용하지 않는다.

### 예시

- 새로운 성향
- 새로운 가치 우선순위
- 장기 목표 변경 가능성
- 위험 선호 변화

Thomas 검토 후에만 Core로 승격한다.

---

## 12.7 Memory Save Criteria

다음 중 하나 이상이면 저장을 검토한다.

- 향후 판단에 재사용 가능
- 반복되는 Thomas 선호
- 중요한 결정과 이유
- 예상과 실제 결과의 차이
- 반복 방지가 필요한 실패
- 새로운 운영 교훈
- 장기 목표 관련 정보

Thomas가 직접 말한 정보도 일회성 지시인지 지속적 선호인지 구분한 뒤 저장한다.

직접 발언이라는 이유만으로 자동 저장하거나 Core에 반영하지 않는다.

---

## 12.8 Memory Exclusion

다음 정보는 기본적으로 저장하지 않는다.

- 의미 없는 일상 대화
- 일회성 세부 정보
- 중복 정보
- 근거 없는 추론
- 미래 활용 가치가 낮은 정보
- 필요 이상의 개인 정보
- 비밀번호, API Key, 인증 Token, 복구 코드 등 인증 정보
- 저장 목적과 접근 권한이 정의되지 않은 민감 정보

---

## 12.9 Memory Confidence

```text
Thomas Direct

→ Highest

Thomas Approved

→ High

Repeated Observation

→ Medium

Single Observation

→ Low

Agent Inference

→ Candidate Only
```

신뢰도는 저장 여부를 단독으로 결정하지 않는다. 근거, 민감도, 유효기간, 재사용 가치, 충돌 여부를 함께 검토한다.

---

## 12.10 Memory Promotion Responsibility

MVP의 Memory 승격 책임은 다음과 같다.

```text
Agent
→ Memory Candidate 생성

Validation Agent
→ 근거, 중복, 충돌, 민감도 검토

Thomas Prime
→ 낮은 위험의 운영 Memory를 Validated Memory로 승격 또는 거절

Thomas
→ Core Candidate 승인 또는 거절
```

Thomas Prime은 Identity, Values, 장기 목표, 위험 기준, Permission Policy 관련 Memory를 직접 승격할 수 없다.

향후 Memory 양과 복잡성이 증가하면 별도의 Memory Curator Agent가 승격 검토를 담당할 수 있다. 이 Agent도 Core 승격 권한은 갖지 않는다.

---

# 13. Failure and Recovery Policy

## 13.1 Failure Principle

실패를 숨기지 않는다.

추가 피해를 먼저 방지한다.

증거를 보존한다.

복구 가능한 범위에서 자율 복구한다.

---

## 13.2 Failure Process

```text
Failure Detected

↓

Stop Unsafe Action

↓

Preserve State

↓

Identify Cause

↓

Attempt Safe Recovery

↓

Validate Recovery

↓

Report if Required

↓

Store Lesson
```

---

## 13.3 Automatic Retry

### 자동 재시도 가능

- 일시적 연결 오류
- 복구 가능한 Program 오류
- 낮은 위험 Tool 오류

### 자동 재시도 금지

- 권한 오류
- 보안 오류
- 데이터 손상 가능성
- 고위험 외부 행동 실패

---

## 13.4 Execution Budget and Limits

모든 Task는 실행 전에 사용량 한도를 가져야 한다.

MVP 기본 한도는 다음과 같다.

```yaml
max_parallel_agents: 3
max_total_agent_calls: 12
max_revision_cycles: 2
max_validation_cycles: 2
max_retry_count_per_step: 3
max_task_runtime_minutes: 30
token_budget: required_runtime_setting
cost_budget: required_runtime_setting
```

`token_budget`와 `cost_budget`는 서버 운영 설정에서 반드시 숫자로 지정한다. 값이 없으면 자율 Task를 시작하지 않는다.

Task 특성에 따라 더 낮은 한도를 설정할 수 있다. 기본 한도를 높이려면 Thomas의 Task별 승인이 필요하다.

한도에 도달하면 다음 절차를 따른다.

1. 새로운 Agent, Tool, Program 호출을 중지한다.
2. 현재까지의 결과와 실행 상태를 보존한다.
3. Task를 `BLOCKED`로 전환한다.
4. 사용량, 반복 원인, 남은 작업, 수정이 필요한 이유를 Thomas에게 보고한다.
5. `한도 1회 확장`, `계획 수정`, `현재 결과로 종료`, `취소` 선택지를 요청한다.

한도는 Agent가 자동으로 늘릴 수 없으며, 승인은 해당 Task에 한 번만 적용한다.

---

# 14. Learning Policy

학습은 실행 중인 운영 규칙을 조용히 변경하는 행위가 아니라, 검증 가능한 개선 후보를 만드는 과정이다.

```text
Observation
↓
Improvement Candidate
↓
Limited Trial
↓
Validation
├─ Activate
└─ Rollback
```

Agent는 다음 항목에 대한 Improvement Candidate를 만들 수 있다.

- 업무 순서
- Tool 선택
- 보고 형식
- 검색 방법
- 낮은 위험 프롬프트와 역할 지시
- 재시도 방법
- 작업 분해 방법

학습 후보는 다음 조건을 모두 충족해야 시험할 수 있다.

- Thomas의 직접 피드백이 있거나, 완료된 Task 3건 이상에서 동일한 개선 필요성이 반복된다.
- 기대 효과를 정확성, 시간, 비용, 재시도 횟수 중 하나 이상으로 측정할 수 있다.
- 적용 범위와 대상 Role, Program, Tool이 명확하다.
- 변경 전 버전을 보존하고 즉시 되돌릴 수 있다.
- 별도의 제한된 Task 또는 시험 문맥에서 먼저 검증한다.
- 실행 사용량 한도 안에서 시험한다.
- 외부 행동 대상, 보안, 권한, 위험 기준을 변경하지 않는다.

낮은 위험의 제한 시험은 Thomas Prime이 실행하고 결과를 보고할 수 있다.

여러 Task의 기본 동작, 공통 프롬프트, 조직 전체 라우팅 규칙에 적용하려면 Thomas 승인이 필요하다.

Agent는 다음을 학습 대상으로 삼거나 변경할 수 없다.

- Thomas Identity
- Thomas Core Values
- 장기 목표
- 위험 기준
- Permission Policy
- 자신의 권한
- Operating Constitution

시험 결과가 기존 성능보다 나쁘거나 새로운 위험을 만들면 즉시 이전 버전으로 되돌리고 Audit에 기록한다.

---

# 15. Audit Policy

모든 Task는 최소 실행 기록을 남긴다.

- Task ID
- 목표
- 요청자
- Trace ID와 주요 시각
- 적용한 Core, Policy, Role, Program 버전
- 사용 Agent Role
- 사용 Program
- 사용 Tool
- 주요 입력
- 주요 결정
- 결정 이유
- 위험 수준
- 승인 기록
- 실행 결과
- 검증 결과
- Memory 결과
- 완료 상태

다음 행동은 결과만 기록하지 않고 요청, 판단, 실행을 각각의 Audit Event로 남긴다.

- Permission Decision과 승인 사용
- 외부 행동
- 파일 또는 데이터 삭제
- Validated Memory와 Core Candidate 변경
- Improvement Candidate 시험, 활성화, 되돌리기
- 실행 한도 초과와 한도 확장

Audit 저장소는 append-only 방식으로 운영한다.

Agent는 자신의 Audit 기록을 삭제하거나 변경할 수 없다. 정정이 필요하면 기존 기록을 보존하고 새로운 정정 Event를 추가한다.

---

# 16. MVP Non-Goals

MVP는 다음을 목표로 하지 않는다.

- 모든 전문 부서 구현
- 수십 개 Agent 상시 운영
- 완전 자율 사업 생성
- 자동 회사 설립
- 자동 채용
- 자동 계약
- 자동 결제
- 자동 투자
- 자동 실거래
- 자동 권한 확대
- Thomas 없는 최고 의사결정

---

# 17. MVP Success Criteria

MVP는 다음 조건을 충족하면 성공으로 평가한다.

- Thomas Core를 실제 업무 판단에 사용
- Thomas Prime이 업무를 올바르게 분류
- Agent와 Program 역할 분리
- 최소 팀 동적 구성
- 낮은 위험 업무 자율 수행
- 복합 업무 독립 검토
- 위험 행동 승인 요청
- 중요한 기억 재사용
- 실패 기록과 안전한 복구
- 사용량 한도 준수와 한도 초과 시 안전한 중지
- 결과 보고가 Thomas Preference와 일치

---

# 18. Final Operating Rules

> 모든 업무는 명확한 목표를 가져야 한다.

> 모든 업무는 실행 전에 복잡도와 위험을 분류해야 한다.

> 새로운 판단은 Agent가 담당한다.

> 반복·계산·검사는 Program이 담당한다.

> 필요한 최소 팀만 활성화한다.

> 생성과 검토는 필요에 따라 분리한다.

> 외부 행동은 권한과 위험 검사를 통과해야 한다.

> 낮은 위험 업무는 자율적으로 처리한다.

> 중요한 위험과 예외만 Thomas에게 요청한다.

> 승인은 정확한 행동, 대상, 범위, 유효기간에 연결한다.

> 모든 정보를 기억하지 않는다.

> 중요한 결정·경험·교훈만 검증하여 저장한다.

> Agent는 스스로 발전할 수 있지만 자신의 권한을 확대할 수 없다.

> 실행과 학습은 사용량 한도 안에서 수행하며 한도를 자동으로 확대하지 않는다.

> 미래 확장은 가능하게 유지하되 현재 MVP를 무겁게 만들지 않는다.
