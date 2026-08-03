# 제안: Conversational Orchestration Front v0.1 — F1·F2 구현 완료, F3 미착수

> **상태 (2026-07-30 갱신): 더 이상 미착수 제안이 아니다.** §7의 D1–D3는 결정·구현되어
> 프로덕션에서 가동 중이고, D4(standing grant)만 미결이다. 항목별 현재 상태는 §7의 표에,
> 구현이 제안을 벗어난 지점은 §7.1에 있다.
>
> **§1–§6 본문은 2026-07-25 제안 원문 그대로 둔다.** 무엇을 제안했고 무엇이 그 모양대로
> 지어졌는지의 대조가 이 문서에 남은 값이기 때문이다 — 본문을 현재 사실로 고쳐 쓰면 그
> 대조가 사라진다. 따라서 **본문의 미래시제 문장은 현재 사실의 진술이 아니다.**
>
> **이 문서는 어떤 것의 authority도 아니다.** 지어진 두 파트의 계약은 예고대로 영문
> runtime-contract가 됐다 — `docs/runtime-contracts/FRONTDESK_V0.1.md`,
> `docs/runtime-contracts/TASK_REGISTRY_V0.1.md`. 그 밖에 역할 상태는
> `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml`, 권한·효과는 `governance/GOVERNANCE_POLICY.yaml`,
> 구현 사실은 `docs/ACTIVE_ARCHITECTURE.md`, 왜 그 모양인지는 `docs/BUILD_HISTORY.md`.
> 설계 기준은 `docs/THOMAS_AUTONOMOUS_ORGANIZATION_ARCHITECTURE.md`(§12–§16)이며 그대로다.
>
> 상태 확인 기준: `main` = `a0f281c` (2026-07-30). 아래 상태는 레지스트리·스키마·정책
> 파일에서 직접 확인했다.

---

## 0. 목표

**"텔레그램에서 Claude Code와 대화하듯 일한다."**

구체적으로:
- 자연어 대화로 작업을 지시하고, 되묻고, 다듬는다 (명령어 프리픽스 강제 없음).
- "지금 뭐 하는 중이야?", "어제 그 분석 결과 보여줘", "얼마나 됐어?"가 대화로 동작한다.
- 여러 작업을 하나의 텔레그램 채널에서 등록·추적·수령한다.
- 한 번 명시적으로 승인한 종류의 행동은 (경계 안에서, TTL 동안) 다시 묻지 않는다.

이것을 **fail-closed 정체성을 깨지 않고** 달성하는 것이 이 설계의 전부다.
방법은 하나: **대화하는 액터와 권한을 가진 액터를 분리**한다.

## 0.1 설계 원칙 (기존 가드레일 재확인)

- **Reuse first** — 새 개념 3개(프론트 역할, Task Registry 레코드, standing grant)는 각각
  기존 owner(role registry / ledger·jsonl 스토어 / `approval.v0.2`)의 확장이다. 신규 발명 없음.
- **Fail-closed** — 모든 새 경로는 실패 시 BLOCK + 안정적 `reason_code`. 아래 각 파트에 명시.
- **One concept = one authority** — 계획·라우팅은 Prime, 상태 장부는 Registry, 승인은 approval
  레코드. 프론트 에이전트는 이 중 무엇의 authority도 아니다.
- **Safety-Flag Gate** — 프론트 에이전트의 대화용 LLM 호출도 provider 게이트 대상.
  좋은 대화 경험은 다음 능력의 자동 승인이 아니다.

---

## 1. 아키텍처 개요

```
        [Thomas] ⇄ Telegram (R4 신원 게이트: private 1:1, 등록된 user+chat, 미검증 무시)
                       │
        ┌──────────────▼──────────────────┐
        │ ① 대화형 프론트 에이전트          │   LLM · 저권한 (P2 ceiling)
        │   conversation.frontdesk         │   세션 맥락 · 되묻기 · 요약/보고
        │   권한: READ + intake + report만  │   계획 X · spend X · effect X
        └──────┬───────────┬──────────────┘
               │            │
        작업 제출/취소      상태·이력·결과 조회 (read-only)
               │            │
        ┌──────▼────────────▼─────┐
        │ ② Task Registry          │   결정론적 append-only 장부
        │   task_registry.jsonl    │   QUEUED→RUNNING→(DELIVERED|FAILED|BLOCKED)
        └──────┬──────────────────┘
               │ 순차 실행 (기존 single-process overlap-safe 모델 유지)
        ┌──────▼──────────────────────────────┐
        │ 기존 거버넌스 코어 (변경 없음)          │
        │ Prime 계획 → 역할 라우팅 → 게이트     │
        │ → 파이프라인 → 감사 → ledger          │
        └──────┬──────────────────────────────┘
               │ effect가 APPROVAL_REQUIRED를 만나면
        ┌──────▼──────────────────┐
        │ ③ Approval / Standing    │   R9 ask → Thomas /approve
        │    Grant (approval.v0.3) │   승인 1건 → (경계·TTL 안에서) 재사용 grant
        └─────────────────────────┘
```

핵심 불변식: **프론트 에이전트가 무엇을 말하든, effect는 전부 ②→코어→③을 통과한다.**
프론트가 뚫려도(프롬프트 주입, 모델 오작동) 얻는 것은 "읽기 + 작업 제출 요청"뿐이며,
제출된 작업 자체가 다시 전체 거버넌스를 통과한다.

### 단계적 도입

| 단계 | 내용 | 거버넌스 변경 |
|---|---|---|
| **F1** | Task Registry + 텔레그램 조율 verb (대화 아님, 결정론) | 없음 (zero contract/schema/gate) |
| **F2** | 대화형 프론트 에이전트 (F1 위에서 대화 인터페이스) | 역할 1개 활성화 + provider 그랜트 |
| **F3** | Standing grant (승인 1건 → 경계 지어진 재사용) | `approval.v0.3` + 정책 + 게이트 확장 |

각 단계는 독립적으로 가치가 있고, 각각 별도 Thomas 결정으로 켠다. F1은 F2 없이도 유용하고
(명령어로 다 됨), F2는 F3 없이도 동작한다(승인을 매번 물을 뿐).

---

## 2. Part ① — 대화형 프론트 에이전트 (`conversation.frontdesk`)

### 2.1 역할 정의 (role contract 요지)

`03_ROLE_CONTRACTS/ROLES/`에 신규 역할 문서 1개 + `ROLE_REGISTRY.yaml` 항목 1개.
기존 `role_registry.v0.3` 스키마 그대로 (스키마 변경 없음).

```yaml
role_id: conversation.frontdesk
role_type: session_front            # dynamic_specialist 아님 — 작업을 수행하지 않는다
status: candidate → active          # 활성화는 Thomas 결정
routable: false                     # Prime이 이 역할로 작업을 라우팅할 수 없음 (핵심!)
```

- **`routable: false`가 권한 분리의 반쪽이다.** 프론트는 Prime의 라우팅 대상이 아니므로
  파이프라인 안에서 specialist 자리에 앉을 수 없다. 프론트는 파이프라인 *앞*에만 존재한다.
- **P2 ceiling** (validation.independent와 동일 tier). 허용 scope:
  - `INTERNAL_READ` — Registry 조회, ledger 조회(read-only), VALIDATED 메모리 읽기
  - `INTERNAL_ANALYSIS` — 대화 이해/응답 생성을 위한 자체 LLM 호출 (R7.2 triage의 선례:
    orchestration-support 목적의 소형 governed 모델 호출)
- **명시적 금지 (contract에 못박음):**
  - `WORKSPACE_REVERSIBLE_WRITE` 이상 모든 effect scope — 프론트는 쓰지 않는다
  - 검색/툴 호출 — 프론트는 조사하지 않는다 (조사가 필요하면 작업으로 제출)
  - PermissionDecision 발행 — 계획은 Prime의 authority
  - approval 발행/소비 — 승인 요청은 코어가 만들고, 결정은 Thomas가 한다

### 2.2 대화 → 행동 번역 (닫힌 출력 계약)

프론트의 LLM 출력은 자유 텍스트가 아니라 **닫힌 스키마의 구조화 응답**이다
(`frontdesk_turn.v0.1`, closed schema, `additionalProperties: false`):

```
{ turn_kind: SUBMIT_TASK | QUERY_STATUS | QUERY_HISTORY | QUERY_RESULT
            | CANCEL_TASK | CLARIFY | CHAT_REPLY
, payload: {...턴 종류별 닫힌 필드...}
, reply_text: "사용자에게 보낼 자연어 응답" }
```

- `SUBMIT_TASK`의 payload는 **원문 요청 텍스트 + 옵션 플래그**(중요도, 독립검증 등)만 담는다.
  프론트가 요청을 "재해석/요약해서" 제출하지 않는다 — **원문이 파이프라인에 들어간다**
  (lossy paraphrase 금지; 프론트의 재구성은 `reply_text`에서 확인용으로만 보여준다).
- 스키마 불일치 출력 → 그 턴은 `CHAT_REPLY`로 강등 + `FRONTDESK_TURN_INVALID` 감사.
  **불확실하면 아무것도 제출하지 않는다** (fail-closed는 여기서도).
- `CANCEL_TASK`는 QUEUED 상태의 작업만 (RUNNING 취소는 kill-switch의 영역 — 침범 금지).

### 2.3 세션 상태 (Reuse: R5 working memory)

- 대화 스레드 상태는 **R5 working-memory 후보 확장**으로 저장 (새 저장소 발명 금지).
  scope: `frontdesk_session` (해당 역할의 `memory_policy.readable_scopes`에만 부여 —
  specialist/validator는 이 scope를 읽지 못한다: 대화 맥락이 작업 컨텍스트를 오염시키지 않음).
- 기존 retention 의미론 그대로: `expires_at` 필수, prune 대상, VALIDATED 승격은 별도 R9 승인.

### 2.4 Provider (Reuse: Safety-Flag Gate 체인 의미론)

- `MVP_FRONTDESK_PROVIDER` — `MVP_VALIDATOR_PROVIDER`와 동일한 의미론: 자체 per-machine
  그랜트, 미인가 멤버 포함 체인은 전체 fail-closed, env var 단독으로는 아무것도 안 켜짐.
- 대화는 호출 빈도가 높으므로 **자체 토큰 allowance**(R7.2 `TRIAGE_TOKEN_ALLOWANCE` 선례)
  + 턴당 예산 기록.
- 프론트 provider 장애 시: **대화만 죽고 런타임은 산다.** 결정론 verb(F1)가 폴백이며,
  `FRONTDESK_DEGRADED` 감사 후 명령어 모드 안내 메시지를 보낸다 (R3 `SEARCH_DEGRADED` 선례).

### 2.5 kill-switch 결합

- 프론트의 LLM 호출은 `kill_blocks` 대상 (PAUSED/KILLED에서 대화 LLM 정지).
- 단, R4 콘솔 verb(`status`/`audit`/`recovery` 등)와 F1 read-only 조회 verb는
  기존 `kill_allows: [read_only_status, audit_read]` 의미론대로 계속 응답한다 —
  죽은 런타임의 상태를 물어볼 수단은 항상 남는다.

---

## 3. Part ② — Task Registry + 텔레그램 조율 verb

### 3.1 왜 필요한가

현재 런타임은 intake 1건 동기 one-shot이다. `trace_id`는 있으나 "상태를 가진 작업 목록"
이라는 개념이 없다. "지금 뭐 하는 중 / 뭐 했는지 / 얼마나 됐는지"는 전부 이 장부 하나에서
나온다. **F1은 거버넌스 변경 zero** — 새 레코드 kind 1개와 read-only verb들뿐이다.

### 3.2 레코드: `task_registry_entry.v0.1` (closed schema)

기존 `task.v0.3`(작업 자체의 authority)을 **재발명하지 않는다.** Registry 항목은 작업의
*조율 상태*만 들고, 작업 실체는 `task_id`/`trace_id`로 참조한다.

```
{ schema_version: "task_registry_entry.v0.1"
, registry_entry_id            # integrity.short_id (결정론 id 관례)
, task_id, trace_id            # 기존 파이프라인 레코드 참조 (실행 전엔 null)
, request_text                 # 원문
, origin: TELEGRAM | CLI | SCHEDULER | FRONTDESK
, flags: { important, independent_validation, revise, write_output }
, status: QUEUED | RUNNING | DELIVERED | FAILED | BLOCKED | CANCELLED
, submitted_at, started_at, finished_at        # UTC, timeutil 관례
, result_ref                   # 전달물 위치 (ledger kind / workspace 경로)
, last_reason_code             # FAILED/BLOCKED 시 typed reason
}
```

- 저장: `.runtime_governance_state/task_registry.jsonl` — `schedules.jsonl`과 동일 패턴
  (local, gitignored, `filelock` per-file 잠금, append-only + 상태 전이는 CAS).
- 상태 전이는 **forward-only** (programization 선례): QUEUED→RUNNING→종결 3종,
  QUEUED→CANCELLED만 허용. 역방향/건너뛰기 시도는 `REGISTRY_TRANSITION_INVALID`.
- 손상된 행: 그 행만 `CORRUPT`로 보고하고 조회는 계속 (store `health` 선례) —
  단, **쓰기 경로는 fail-closed** (손상 감지 시 새 제출 거부).

### 3.3 실행 모델: 큐 + 순차 (동시성 없음 — 명시적 비확장)

- 기존 single-process overlap-safe 모델을 **그대로 유지**한다. Registry는 가시성을 주는
  장부이지 병렬 실행기가 아니다.
- 워커 = 기존 operator 루프 확장: 배치 처리 시 Registry에서 QUEUED를 claim
  (`claim_due`의 잠금 패턴 재사용) → 기존 파이프라인 실행 → 상태 갱신.
- 스케줄러 fire도 동일 Registry에 기록 (origin: SCHEDULER) — **모든 작업이 한 장부에**.
- 진짜 병렬 실행(worker pool, 동시 예산)은 **이 제안의 범위 밖**이며 별도 Thomas 결정.

### 3.4 텔레그램 verb (F1 — 결정론, 대화 아님)

R4 identity gate 뒤의 operator 루프 명령 세트에 추가 (`/approve`/`/reject`가 있는 층위 —
비상 콘솔 `control.COMMANDS`가 아니므로 `emergency_controls_allowed` 정책·드리프트 게이트
비침범):

| verb | 동작 | 성격 |
|---|---|---|
| `/tasks` | QUEUED+RUNNING 목록 (id, 요청 요약, 상태, 경과) | read-only |
| `/history [n]` | 최근 종결 n건 (기본 10) | read-only |
| `/result <id>` | 해당 작업의 전달물 재전송 (4096-chunk 재사용) | read-only |
| `/cancel <id>` | QUEUED만 취소 → CANCELLED + 감사 | 상태 전이 |
| (기존) 작업 텍스트 | Registry 등록 후 실행 | 기존 경로 |

- read-only 3종은 ledger 이벤트를 쓰지 않는다 (`status` verb의 "읽기가 자기 체인 팁과
  경주하지 않는다" 선례). `/cancel`은 감사 이벤트를 남긴다.
- **진행률에 대한 정직성:** 파이프라인은 단계형이므로 진행 표시는 %가 아니라
  **단계 이름**(intake / planned / model_call / validating / delivering)이다.
  가짜 퍼센트를 만들지 않는다.

---

## 4. Part ③ — Standing Grant: `approval.v0.3` (승인 1건 → 경계 지어진 재사용)

### 4.1 원리

> "내가 승인한 것에 대해서는 권한을 부여한다." (Thomas, 2026-07-25)

권한의 출처는 사전 화이트리스트가 아니라 **검증된 operator가 실제로 내린 승인 1건**이다.
에이전트는 스스로 권한을 넓히지 못한다 — 넓히는 것은 언제나 Thomas의 명시적 `/approve`다.
이는 R9 승인의 **확장**이지 새 권한 객체가 아니다 (Reuse first).

### 4.2 스키마: `approval.v0.2 → v0.3` (additive)

현행 v0.2는 `consumption.one_time_use: const true`. v0.3은 이를 선택 가능하게 한다:

```
consumption:
  one_time_use: boolean          # const true → boolean (기본 true; 기존 의미 보존)
  ...기존 필드 유지...

standing:                        # one_time_use=false일 때만 존재 (조건부 required)
  generalization:                # Tier-2 일반화 키 — 이 grant가 커버하는 것의 전부
    permission_scope             # 정확히 1개 scope (예: WORKSPACE_REVERSIBLE_WRITE)
    action_type                  # 정확히 1개 (예: CREATE)
    target_pattern               # 자원 경계 (예: "workspace/reports/*") — 와일드카드는
                                 #   마지막 세그먼트 1개만; 경로 이스케이프 검사는
                                 #   R8 workspace 격리 로직 재사용
  max_uses                       # 정수 상한 (무한 없음)
  uses: [ { used_at, trace_id, consumption_ref } ]   # 사용 1건 = 항목 1건 (감사 필수)
  revoked: { revoked_at, reason } | null
```

- v0.2 레코드는 전부 v0.3에서 유효 (additive; `programization_pattern` v0.1→v0.2 선례).
- `action_fingerprint`는 유지하되, standing grant의 매칭은 fingerprint 동일성 대신
  **generalization 키 일치**로 판정한다. 판정 함수는 단 하나의 모듈에 산다 (one authority).

### 4.3 재사용 판정 (매 사용마다, hot-path 재검증)

R10 소비의 가드를 전부 상속하고 세 가지를 더한다:

1. **kill-switch 먼저** (`RUNTIME_KILLED`/`RUNTIME_PAUSED` — 기존 거부 어휘)
2. grant 상태 = APPROVED-standing이고 `revoked: null`이고 TTL 내
3. 요청 액션이 generalization 키에 **정확히** 일치 (scope+action_type+target_pattern 모두)
4. `uses 길이 < max_uses` — CAS로 사용 항목 append (동시 사용 경쟁 시 한쪽은
   `GRANT_EXHAUSTED`; R10 "CONSUMED before promotion" 선례대로 **사용 기록 먼저, 실행 나중** —
   실패한 실행은 사용 1회를 소모한 채 남는다, 재사용 가능한 채 남지 않는다)
5. 불일치/모호 → **그 자리에서 일반 R9 단발 승인으로 강등** (`GRANT_MISS_ASK_FALLBACK` 감사)
   — standing grant의 실패는 차단이 아니라 "다시 물음"이다

### 4.4 발급·취소 UX

- 발급: 일반 승인 요청에 선택지가 하나 늘어난다 —
  `/approve <id>` (이번만) vs `/approve <id> standing [uses=N] [ttl=…]` (이후 같은 패턴 포함).
  **standing은 언제나 opt-in 문법**이다; 기본 `/approve`는 영원히 단발이다.
- 발급 시 봇이 **grant가 커버하는 것을 자연어로 되읽어준다** (M5c correction-readback 선례):
  "앞으로 [TTL]동안 workspace/reports/ 아래 파일 생성은 다시 묻지 않습니다 (최대 N회).
  /revoke <grant_id>로 언제든 취소."
- `/grants` — 활성 standing grant 목록 (read-only). `/revoke <grant_id>` — 즉시 취소 + 감사.
- **kill-switch는 모든 standing grant 사용을 즉시 정지**한다 (취소가 아니라 정지 —
  resume 후에도 grant 자체는 TTL/uses가 남아 있으면 유효).

### 4.5 정책 경계 (governance/GOVERNANCE_POLICY.yaml 추가분)

```
standing_grant:
  eligible_scopes: [WORKSPACE_REVERSIBLE_WRITE]     # 시작은 정확히 1개
  max_ttl_days: 7                                   # 단발 승인 TTL(15/30일)보다 짧게
  max_uses_ceiling: 20
  never_eligible:                                   # 영구 제외 — 문서가 아니라 정책으로
    - SENSITIVE_MEMORY_GOVERNANCE
    - CANDIDATE_ROLE_TRIAL
    - TOOL_PROGRAM_GOVERNANCE
    # + 외부호출/금융 등 향후 모든 APPROVAL_REQUIRED 이상 scope는 기본 제외;
    #   eligible 추가는 건별 Thomas 결정 + 정책 버전업
```

- `WORKSPACE_REVERSIBLE_WRITE`가 유일한 첫 대상인 이유: 이미 EXECUTE_AND_REPORT,
  create-only, 구조적 가역, `workspace/` 격리, kill-bound, `filesystem_write` 플래그 뒤.
  즉 **이미 다섯 겹 뒤에 있는 가장 낮은 effect**에만 "다시 묻지 않기"를 허용한다.
- APPROVAL_REQUIRED scope들의 standing 제외는 R9의 존재 이유와 일치한다 — Prime의 조건부
  P4가 검증 메모리 변경을 배제하는 것과 같은 선.

### 4.6 게이트 변경 (좁게)

- permission 게이트: standing-grant 경로는 **eligible_scopes에 있는 scope의
  EXECUTE_AND_REPORT 액션에만** 새 disposition으로 인정. APPROVAL_REQUIRED/BLOCK의
  buildable/executable 구분(R9/R10)은 변경 없음.
- 검증기·릴리스 게이트: `standing` 블록 스키마 검증 + `never_eligible` 드리프트 게이트
  (eligible ∩ never_eligible = ∅ 상시 검사) 추가.

---

## 5. 지켜지는 불변식 (이 설계가 깨지 않는 것)

1. 프론트 에이전트는 **effect를 낼 수 없다** — 어떤 코드 경로로도 (routable: false + P2 +
   scope 2개 + 출력 스키마 강제).
2. 모든 effect는 지금과 같은 게이트를 지나며, standing grant가 완화하는 것은
   **"물어보는 시점"뿐, "감사 여부/기록 여부"가 아니다** (사용 1건 = 감사 1건).
3. 권한 확장의 주체는 항상 검증된 Thomas의 명시적 행동이다 (`standing` opt-in 문법).
4. kill-switch는 모든 새 경로 위에 있다 (대화 LLM, 큐 실행, grant 사용 전부 kill_blocks).
5. 읽기·상태조회는 죽어도 남는다 (kill_allows 의미론 유지).
6. 어떤 실패도 침묵하지 않는다 — DEGRADED/INVALID/EXHAUSTED/FALLBACK 전부 typed + 감사.

## 6. 명시적 비목표 (이 제안이 하지 않는 것)

- 병렬 작업 실행 (worker pool) — 별도 결정
- APPROVAL_REQUIRED scope의 standing grant — `never_eligible`로 정책 차단
- 프론트 에이전트의 도구/검색/쓰기 — 역할 계약으로 금지
- RUNNING 작업 취소 — kill-switch의 영역
- Executor handoff / 외부 / 금융 effect — 기존 로드맵 문구 그대로 별도 결정

## 7. Thomas 결정 — 현재 상태 (2026-07-30)

| # | 결정 | 단계 | 상태 |
|---|---|---|---|
| ~~D1~~ | Task Registry + 조율 verb 구현 착수 | F1 | ✅ **구현 완료 2026-07-25** (`c8dbbab`) — `task_registry.jsonl` + `/tasks` `/history` `/result` `/cancel` |
| ~~D2~~ | `conversation.frontdesk` 역할 활성화 (registry `status: active`) | F2 | ✅ **승인·활성 2026-07-25** (`122f2d0`) — 레지스트리 `status: active`, `routable: false`는 일관성 검증기가 영구 고정. 역할 정의는 이후 `0.5.0`까지 감 |
| ~~D3~~ | 프론트 provider 그랜트 (해당 머신 `activate_safety_flag`) | F2 | ✅ **이 머신에서 충족** — `MVP_FRONTDESK_PROVIDER=groq`, 그랜트는 per-machine·gitignored이므로 **다른 머신에는 각각 다시 필요** |
| **D4** | `approval.v0.3` + standing_grant 정책 + 게이트 확장 | F3 | ❌ **미착수** — approval은 여전히 `v0.2`(`consumption.one_time_use: const true`), 정책에 `standing_grant` 블록 없음. `docs/proposals/APPROVAL_CONVERSATION_V0.1.md` §6에 **V4**로 다시 올라와 있다 |
| D5 | (F3 이후 건별) eligible_scopes에 scope 추가 | — | D4 대기 |

원문의 권장 순서(D1 → 운용 → D2/D3 → 필요가 확인되면 D4)에서 실제로 달랐던 것은 **간격**
하나다: D1과 D2/D3는 운용 관측 없이 같은 날 연달아 랜딩했다. D4는 권장대로 아직 열려 있고,
"승인 왕복이 실제로 거슬리는 지점"은 이후 `APPROVAL_CONVERSATION` 제안이 다시 묻고 있다.

### 7.1 구현이 제안을 벗어난 지점

본문을 고치지 않는 대신 여기에 모은다. 각 항목은 왜 벗어났는지가 요점이다.

- **턴 종류 7종 → 10종** (`frontdesk_turn.v0.1` → `v0.2`, 2026-07-25). §2.2 목록에
  `QUERY_SCHEDULES` / `QUERY_CONTROL` / `QUERY_MEMORY`가 더해졌다 — 대화가 물어볼 상태는
  Registry 하나가 아니었다.
- **`CANCEL_TASK`는 프론트가 처분하지 않는다** (`frontdesk._propose_cancel`). §3.4는 프론트의
  취소를 상태 전이로 그렸지만, 구현은 **칠 명령을 제안**만 하고 처분은 operator의 `/cancel`이
  한다 — 읽기 전용으로 구성. §5 불변식 1을 제안보다 좁게 지킨 결과다.
- **역할 라우팅이 대화에 들어왔다** (`frontdesk_turn.v0.3` + `task_registry_entry.v0.2`의
  `request_kind`, 2026-07-27). 제안 시점엔 활성 역할이 둘뿐이라 없던 문제다. v0.1 행은
  `null`로 읽히고, 그 값이 그 행들이 실제로 돈 analysis 라우팅이다.
- **되묻기가 답을 실어 나른다** (`frontdesk_turn.v0.4`의 `clarification_texts`, 2026-07-28).
  §2.2의 `CLARIFY`는 질문하고 턴을 끝내서, Thomas의 답을 원 요청과 합쳐 제출할 길이 없었다.
- **provider는 체인이 아니라 단일이다.** §2.4는 `MVP_HOSTED_PROVIDER`와 같은 페일오버 체인
  의미론을 적었으나 구현·운용은 멤버 하나(`groq`)다. 2026-07-29 그 단일 키의 프리티어 쿼터를
  프롬프트 측정 작업이 소진해 운영 채널이 degrade됐고(fail-closed, 메시지 손실 없음),
  체인화 여부는 `docs/REMAINING_WORK.md`의 열린 항목이다.

---

*작성: 2026-07-25, Thomas ↔ Claude 설계 대화 (비서 vs 오케스트레이터 → 조율 레이어 →
대화형 프론트 → standing grant) 의 합의 내용을 문서화. 초안 당시 이 파일은 untracked였고,
같은 날 `09ce869`으로 커밋됐다.*

*상태 갱신: 2026-07-30 (`main` = `a0f281c`) — 상단 상태 블록과 §7/§7.1만 현재 사실로 고쳤다.
§1–§6은 제안 원문 그대로다.*
