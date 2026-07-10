# Thomas Autonomous Organization Document Map

Status: MVP Document Structure v0.1

Owner: Thomas

## 1. Document Hierarchy

문서는 조직 구조와 같은 방향으로 위에서 아래로 구체화한다.

```text
Thomas
└─ Thomas Core
   └─ Operating Constitution (Target, not active in MVP)
      └─ Organization Architecture
         └─ MVP Operating Policy
            └─ Common I/O Contracts
               └─ Role, Agent, Program, Tool Definitions
                  └─ Runtime Records and Audit Events
```

상위 문서는 정체성, 가치, 권한 경계처럼 안정적인 기준을 정의한다.

하위 문서는 실행 방법과 개별 업무 기록처럼 자주 변경되는 내용을 정의한다.

상위 문서와 하위 문서가 충돌하면 상위 문서를 우선한다.

## 2. Active MVP Documents

| Level | Document | MVP Use |
| --- | --- | --- |
| Thomas Core | `../THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | 활성 Core 8개 규칙 |
| Organization Architecture | `thomas-autonomous-organization-architecture-v0.1.md` | Target, MVP, Dynamic Team 구조 |
| Operating Policy | `MVP_OPERATING_POLICY.md` | 실행, 권한, Telegram, Memory, 실패, 학습 규칙 |
| Common I/O Contracts | `thomas-twin-core-architecture-v0.1.md` | Task, Agent Output, Program Result, Permission Decision, Memory Record |
| Prime Charter | `THOMAS_PRIME_CHARTER.md` | Thomas Prime의 역할, 책임, 권한과 금지 경계 |
| Prime Foundation Reference | `thomas-prime-foundation-settings-v0.1.md` | 초기 상세 설정 참고 자료. Active Core와 Charter가 우선 |

## 3. Planned Documents

- `OPERATING_CONSTITUTION.md`: 장기 Governance가 필요해질 때 활성화한다.
- Role Definition: 전문 역할이 반복적으로 필요해질 때 추가한다.
- Program Definition: 실제 Program 구현 전에 추가한다.
- Tool Definition: 실제 Tool 연결 전에 추가한다.
- Runtime schemas: 구현 단계에서 I/O 계약을 기계 검증 형식으로 고정한다.

계획 문서는 존재하거나 활성화된 것처럼 참조하지 않는다.

## 4. Change Rule

- Thomas Core와 Operating Constitution 변경은 Thomas 승인이 필요하다.
- Organization Architecture 변경은 Thomas가 방향을 승인한다.
- MVP Operating Policy의 낮은 위험 세부 조정은 제안과 검토를 거쳐 반영한다.
- Common I/O Contract 변경은 버전을 올리고 기존 기록과 호환성을 확인한다.
- Runtime Record와 Audit Event는 기존 기록을 덮어쓰지 않는다.
