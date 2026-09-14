# Hermes 오케스트레이터 아키텍처 V0.2 — 결정 기록과 불변식

**Status:** 결정됨 (Thomas, 2026-09-03) · **개정 V0.2 (2026-09-14) — workflow manager 도입 결정.** [V0.1](HERMES_ORCHESTRATOR_ARCHITECTURE_V0.1.md)(#826)을 대체한다. 이 문서는 통합 시퀀스 2(§5.2, P00~P11)의 입력이다. 구현·배포·권한 활성화는 이 문서로 승인된 것이 아니다.
**Normative authority:** None — 권한의 정본은 [`governance/GOVERNANCE_POLICY.yaml`](../governance/GOVERNANCE_POLICY.yaml)(1.5.0)이고, 불변식은 코드·테스트·정책이 강제한다. 이 문서는 그것들에 이름을 붙이고, 통합이 무엇을 보존해야 하는지를 적는다.
**Baseline:** `origin/main` `a789bec`(2026-09-14), hermes-agent `9accf79`(V0.1과 같음), 호스트 실측 2026-09-14. 개정에서 다시 확인한 `path:line`은 `a789bec` 기준이고, V0.1에서 그대로 옮긴 인용은 V0.1 기준(`ab9ec51`)이다 — 각 줄에 표시했다.
**근거:** V0.1의 근거에 더해 2026-09-13 서버·소스 검토, 2026-09-14 실행 계획과 그 검토(호스트 `/root/thomas_refactor_plan_2026-09-14/`, 레포 밖). 레포 안의 정본은 이 문서다.

## V0.1에서 바뀐 것

| 항목 | V0.1 (2026-09-03) | V0.2 (2026-09-14) |
|---|---|---|
| 비동기·복합 업무 | "2단계 — 비동기 dispatch(submit→id→poll)" 방향만 | **Workflow Manager**: 계획·단계·시도를 SQLite에 저장하고 dispatch-bridge 프로세스 안의 루프가 접수·claim·dispatch·복구(§3.1 Q18·Q19) |
| 불변식 3 (스케줄) | 변경 verb를 만들지 않는다 | 지금은 그대로. P09에서 **사전 위임 범위의 비금융 일정 변경만** 조건부 허용(§1.3 개정, 미시행) |
| 불변식 4 (실행) | "워커는 registry를 모른다"(§7 재검증) | PR8 이후 거짓 — 워커는 origin `AGENT` 행을 쓴다. 불변식 자체(실행은 워커·레인)는 유지, 재검증 명령 정정(§7) |
| 취소 | 1단계는 타임아웃만 | v2 동기 런은 그대로. workflow attempt는 P06부터 단계 경계 협력 취소(§4.3) |
| 멱등 | `request_id` 재전송 시 `{task_id,status,result}` | v2 그대로. v3는 SQLite `requests`가 권위, `bridge_idempotency`에 claim하지 않음(§3.2) |
| 예산 | Hermes 런당·일일 카운터 + Thomas `cost_used` | **강제(Thomas 측 호출 수·토큰)와 보고(Hermes 측)로 분리**(§3.1 Q24) |
| 알림 | 승인 알림 미러링 | + Operator가 workflow 핵심 이벤트를 푸시하고 `deliveries`에 기록, Hermes는 폴링 서술(§3.1 Q23) |

## 0. 한 화면

```text
         Thomas
           │
           ▼
   ┌──────────────┐
   │    Hermes    │  계획 제안·제출·취소·조회·서술(폴링)
   │ Orchestrator │
   └──────┬───────┘
          │
  Typed / Versioned Door (v2 동기 + v3 workflow.*)
          │
   ┌──────┴────────────────────────────┐
   │                                   │
   ▼                                   ▼
Thomas Control Plane          Read / Dispatch API
   │                          dispatch-bridge ── Workflow Manager 루프(같은 프로세스, uid 10001)
 ┌─┼──────────┐                      │            SQLite .runtime_governance_state/workflow/
 ▼ ▼          ▼                      ▼
Scheduler  Workers  Content/Research  internal/ 워커 소켓 → pipeline-worker(attempt 프레임)
 │ autonomous
 ▼
Crypto / Maintenance / Scheduled Jobs   (Manager·Hermes 장애와 독립)

        별도 권한 Plane
               │
               ▼
        thomas-operator
   Approval / Resume / Emergency + workflow 핵심 이벤트 푸시(best-effort)
```

코드 대응:

| 도식 | 지금의 코드 (`a789bec`) | V0.2 결정 이후(P00~P11) |
|---|---|---|
| Typed / Versioned Door | `socket_door.py` — 개행 JSON 1왕복, `proto`·`client_id`·`data` 프레임(PR7 완료) | v3 `capabilities` 조회와 `workflow.*` 명령(§4.2). 미지 키 거부 규칙 때문에 서버 선배포, shim 후전환 |
| Thomas Control Plane(Hermes가 닿는 부분) | `switch_bridge.py` — `disable`(무승인·즉시·전역), `enable`(항상 승인); read 문 조회 verb `schedules`·`scheduler_events`·`heartbeat`·`approval_status`(PR10 완료) | 그대로. 스케줄 **변경**은 P09의 조건부 위임(§1.3)까지 어느 문에도 없다 |
| Read / Dispatch API | `read_bridge.py`(13 verb, text+`data`), `dispatch_bridge.py`(kind 4종, P3, 동기, `request_id` 멱등·registry `AGENT` 기록 — PR8·PR9 완료) | dispatch-bridge 프로세스 안의 **Workflow Manager 루프**가 v3 접수·claim·dispatch·완료·복구. 조정 상태는 SQLite(§4.4). read 문에 workflow 조회 verb |
| Scheduler (autonomous) | `scheduler.py` 두 레인, `schedules.jsonl`, 컨테이너 내 `scheduler_cli`만이 행을 바꾼다 | 변경 없음. P09에서 일정 변경 **검증 함수**를 CLI와 dispatch 문이 공유(위임 범위 안에서만) |
| Workers | `pipeline_worker.py` — `internal/pipeline.sock`, peer uid 10001만, ASSISTANT_PROFILE 런마다 registry `AGENT` 행(`pipeline_worker.py:301-315`) | P05부터 attempt 프레임(attempt_id·capability·입력 참조) 검증, 결과에 attempt_id·trace_id 에코, origin `WORKFLOW` 행. heartbeat 채널 없음 |
| 별도 권한 Plane | `thomas-operator` — 관제봇 폴링, `/approve`·`/resume`·`/kill`·`/pause`, 승인 요청 푸시 + Hermes 창 미러링(PR11 완료) | + workflow 핵심 이벤트 푸시와 `deliveries` 기록(P08). 결정은 여전히 관제봇 |

## 1. 불변식 8 — 무엇이 지키고 있고, 통합이 무엇을 보존하는가

강제 주체: **code** / **test**(pytest 정확집합) / **policy** / **ops**(배포 체크리스트·실측). "구조적"은 코드 한 줄이 아니라 배치가 보장하는 것. 각 불변식의 **V0.2 개정** 항목은 시퀀스 2가 무엇을 바꾸고 무엇을 바꾸지 않는지를 적는다.

### 1. Hermes = orchestrator

Hermes는 Thomas의 요청을 나누고(dispatch), 결과를 종합하고, 스스로 결정할 수 없는 것을 [상신] 형식으로 가져온다. 이 역할은 레포 밖(`/root/hermes-trial/data/SOUL.md`, `skills/thomas-ops`)에서 정의되며 여기서는 **나머지 일곱이 그 역할의 경계**라는 점만 적는다.

- **V0.2 개정:** Hermes는 계획(`workflow_plan`)을 제안·제출·취소·조회한다. 계획에서 실행 가능한 단계를 고르고 상태를 전이시키는 것은 서버 코드(Manager 루프)이고, 단계의 역할 선택·권한 검사는 기존 Prime이다. Manager는 모델을 호출해 계획을 만들지 않는다. Hermes는 계획을 제출할 뿐 상태를 쓰지 못한다.

### 2. Hermes ≠ approval authority

- 승인 결정(`APPROVED`)의 프로덕션 유일 호출자는 `operator.py:384`(`approval.apply_command`, `a789bec`)이고, 그 앞에 `verify_control_channel`이 선다. **code**
- 어시스턴트가 운반한 승인은 `invalid_approval_sources(other_user, ambiguous_expression)`로 기각된다. **policy** `governance/GOVERNANCE_POLICY.yaml`; 기각 이력은 [`proposals/HERMES_AGENT_SWITCH_V0.1.md`](proposals/HERMES_AGENT_SWITCH_V0.1.md) §8.
- switch `enable`은 게이트 상태와 무관하게 **항상** `APPROVAL_REQUIRED`이며 caller는 `CMD_RESUME`를 이름으로 부를 수 없다. **test** `tests/test_mvp_runtime_switch_bridge.py:160,170`.
- 정직하게 적어 둘 한계(V0.1 그대로): `Verification.method`는 서명 없는 문자열이고 검증은 문자열 일치뿐이다. 유일성은 **정책과 호출자 부재**가 지키는 것이지 암호학적 강제가 아니다. 그래서 "Hermes가 승인을 기록한다"는 어떤 코드도 **정책 위반**으로 다룬다.
- 결정 Q1-b: 승인 요청 **알림**은 Hermes 봇 창에도 미러링한다(PR11 완료). 알림은 결정이 아니다.
- **V0.2 개정:** Manager 루프는 `approval_id`를 **참조만** 한다. `APPROVED`를 만들지 않고, 승인 소비는 기존 `consumption` 경로만 효과를 낸다. 승인이 필요한 단계는 기존 승인 요청 ID·action fingerprint·plan version에 묶이며 계획이 바뀌면 기존 승인으로 진행하지 않는다(P07, 인수 시험 A13).

### 3. Hermes ≠ scheduler authority

- 스케줄 행의 CRUD는 컨테이너 안 `scheduler_cli add/enable/disable/remove`뿐이다. 문 4개 어디에도 schedule **변경** verb가 없다. read 문의 `schedules`·`scheduler_events`는 조회다(`read_bridge.py:79-80,101-102`, `a789bec`). **code + test** `tests/test_mvp_runtime_read_bridge.py:49,58,71`(변경 verb 없음·미지 verb 거부).
- 스케줄러는 Hermes를 호출하지 않는다. 역방향 접점 0. **구조적**
- **V0.2 개정(조건부, 미시행):** P09에서 dispatch 문에 `schedule.propose_change`를 두고, **사전 위임 범위**(비금융 종류 · 최소 간격 · 최대 실행 수 · 유효기간 · 예산) 안의 변경만 직접 적용하며 그 밖은 변경안으로 상신한다. 금융 일정(risk 레인의 `crypto_*`)은 어떤 위임으로도 바꾸지 못한다. 회차의 고유 키는 기존 `schedule_run_id`이며 누락 회차는 종류별 정책(기본 drop, `scheduler.py:434` `next_occurrence`의 at-most-once·catch-up 없음)을 따른다. **시행 조건:** P09 PR(정책·테스트·API를 한 PR에서)과 정책 범프. 그 전까지 read/switch 문의 verb 집합은 그대로이고 위 테스트가 이를 핀한다. P09의 강제 테스트는 A20·A21(`tests/test_mvp_runtime_workflow_schedules.py`).

### 4. Hermes ≠ executor

- 실행은 `pipeline_worker.py`(uid 10001)와 두 스케줄러 레인이 한다. Hermes가 낼 수 있는 것은 dispatch 문의 kind 4종(`analysis/research/translation/content`, P3, `dispatch_bridge._ALLOWED_KINDS`)뿐이며 `development`는 의도적으로 제외돼 있다. **test** `tests/test_mvp_runtime_dispatch_bridge.py:72,78`.
- 워커 소켓은 `internal/`에 있고 `bridge/` 경로를 거부하며 peer uid 10001만 받는다(`docker-compose.yml:566-570` 리터럴) — Hermes 마운트로는 도달 불가. **test** `tests/test_mvp_runtime_pipeline_worker.py:208,217`.
- 문을 통한 모든 효과는 상수 actor `assistant_bridge`로 기록되고 intake에는 `requester_type=agent`로만 들어간다. 역할 레지스트리에 assistant/orchestrator 슬롯은 없고 Hermes는 requester 축이다.
- **정정(V0.1 §7):** "워커는 registry를 모른다(`task_registry` 참조 0)"는 PR8(2026-09-04) 이후 거짓이다. 워커는 ASSISTANT_PROFILE 런마다 origin `AGENT` 행을 best-effort로 열고 닫는다(`pipeline_worker.py:301-315`, 참조 12건, `a789bec`). 실행 주체가 워커라는 불변식은 그대로다. **test** `tests/test_mvp_runtime_task_registry.py:296`(AGENT 행은 워커 집합만 reconcile), `tests/test_mvp_runtime_pipeline_worker.py:690-708`.
- **V0.2 개정:** Hermes가 낼 수 있는 것은 kind 4종의 단일 작업, 또는 그 4종으로만 이루어진 workflow 계획이다(§4.2). Manager 루프는 dispatch-bridge 프로세스(uid 10001) 안에서 돌므로 **워커 소켓의 peer는 여전히 10001 하나**이고 Hermes 마운트로는 도달 불가가 유지된다. P05부터 워커는 attempt 프레임(attempt_id·capability·입력 참조)을 검증하고 origin `WORKFLOW` 행을 쓰며 결과에 attempt_id·trace_id를 에코한다. `WORKFLOW`는 `WORKER_ORIGINS`에 넣지 않아 워커 재기동의 `reconcile_stale_running`(`task_registry.py:481-516`)이 Manager 소유 attempt를 `RUN_ABANDONED`로 바꾸지 못한다(A23). 강제 테스트: 지금 `tests/test_mvp_runtime_task_registry.py:246,274`(origin 집합·schema enum 정확집합); P03에서 `WORKFLOW` 추가 시 같은 테스트 갱신; P05에서 attempt 프레임 정확집합.

### 5. Scheduler survives Hermes failure

- 한 compose 프로젝트(PR5)에서도 **어떤 Thomas 서비스도 hermes를 `depends_on`하지 않고, hermes 헬스가 Thomas 재시작 정책에 들어가지 않는다.** **test** `tests/test_deployment_env_passthrough.py`(`test_no_service_depends_on_any_other`).
- 스케줄러는 Hermes의 존재를 모른다 — 코드 참조 0, 네트워크 의존 0, `depends_on` 없음. **구조적**
- **V0.2 개정:** Manager 루프가 dispatch-bridge 안에 있으므로 새 서비스·`depends_on`·healthcheck 결합이 생기지 않는다. 루프 장애는 dispatch-bridge 장애와 같고(v2·v3 접수가 함께 실패) Risk/Maintenance 레인에 닿지 않는다(A26, `docker-image.yml` 스텝). Manager가 죽고 워커만 사는 새 장애 조합은 만들지 않는다.

### 6. Operator survives Hermes failure

- operator는 관제봇 토큰으로 독립 폴링한다. Hermes 봇은 다른 봇이고, 한 토큰에 폴러는 하나뿐이라 두 봇은 구조적으로 합쳐질 수 없다. **구조적**
- 승인 알림 미러링(Q1-b)은 **best-effort**다. Hermes 창 발신 실패는 operator의 승인 처리·관제봇 푸시에 영향을 주면 안 된다.
- 비상 콘솔(`/kill`·`/pause`·`/resume`)과 호스트 `console_cli`는 Hermes 없이 동작한다.
- **V0.2 개정:** Operator는 workflow 핵심 이벤트(승인 필요, `FAILED`/`BLOCKED`, `COMPLETED`)를 자체 cursor로 읽어 관제봇에 푸시하고 `deliveries`에 `PENDING/CONFIRMED/UNCERTAIN`을 쓴다(P08). 미러링과 같은 규칙 — 푸시 실패가 승인 처리·비상 콘솔에 영향을 주면 안 된다. 같은 이벤트를 푸시하는 주체는 Operator 하나다. Hermes의 서술은 `workflow.events` 폴링이며 전달 보장을 맡지 않는다(A19).

### 7. Hermes cannot read exchange / write secrets

- 문 4개 프로세스의 env는 정확히 `{MVP_BRIDGE_CLIENT_GID, MVP_BRIDGE_CLIENT_UID}`. **test** `tests/test_deployment_env_passthrough.py`.
- 거래소·라이브 변수는 `thomas-scheduler`만 갖는다. **test** 같은 파일. 자금 조회는 스케줄러가 쓴 스냅샷 파일을 read 문이 렌더할 뿐이다.
- Hermes 컨테이너는 `bridge/`와 자체 data만 마운트한다(호스트 실측 2026-09-14: `Binds=[hermes-trial/data, .runtime_governance_state/bridge]`, `GroupAdd=null`). **test** `test_the_assistant_mounts_only_the_bridge_directory_of_the_state_root`.
- 결정 Q9(PR2): 비밀은 **Secret Source of Truth 하나**에서 서비스별로 **필요한 키만** 투영한다(완료).
- **V0.2 개정:** workflow DB는 `.runtime_governance_state/workflow/`에 있고 Hermes는 그 루트를 마운트하지 않으므로 위 테스트가 그대로 핀한다. Manager 루프는 모델·검색·거래·봇 토큰 어느 것도 갖지 않는다 — dispatch-bridge의 env 정확집합이 바뀌지 않기 때문이다(A14).

### 8. Resume requires Thomas approval

- switch `enable`은 ask를 만들 뿐이고, spend는 `APPROVED`·미만료(RUNTIME_GOVERNANCE 15분, `governance/GOVERNANCE_POLICY.yaml:378`)·지문·scope·`stop_ref` 일치·1회용을 모두 통과해야 `CMD_RESUME`에 닿는다. **code + policy**
- `runtime` scope 재개는 `trading_armed`를 되살리지 않는다.
- "Thomas approval"의 다른 형태: Thomas 자신이 관제봇에서 치는 `/resume`과 호스트 `console_cli`. 이 둘은 Hermes 경로가 아니다.
- **V0.2 개정:** 변경 없음. workflow의 `WAITING_APPROVAL`은 Operator 장애나 승인 만료 동안 대기/차단으로 보존되며 Manager가 이를 통과시키지 못한다.

### 파생 규칙 — Hermes의 stop은 GLOBAL_KILL이다 (결정 Q4·Q5)

```text
Hermes
  │
  └─ GLOBAL_KILL
        ↓
Thomas Control State (operator_control_state.json — 도메인 필드 없음)
        ↓
money path / dispatch(v2·v3 접수, Manager 루프의 새 attempt) / 두 스케줄러 레인(claim-and-drop) / 승인 소비  전부 중단
```

- Hermes `disable`(kill·pause)은 무승인·즉시이며 `trading_armed=False`를 함께 쓴다. 도메인별 부분정지는 **없다**(Q4-a).
- Stop 권한의 범위(③-a): Hermes가 **자기 판단**으로 정지할 수 있다. 자동 감시 cron은 정지를 누르지 못하게 코드로 막는다(Hermes 쪽 cron `enabled_toolsets`에서 switch 제외).
- **V0.2 개정:** 정지 중 Manager 루프는 새 attempt를 열지 않는다(dispatch 시점의 제어 상태 검사). 진행 중 attempt는 워커가 완주한다 — 워커의 자체 검사는 시작 시점이다(`pipeline_worker.py:291`). `READY` 단계는 버려지지 않고 재개 후 이어진다. 회차 드롭과 다른 점을 사용자에게 그렇게 표시한다.

## 2. 역할 분담

| 주체 | 한다 | 하지 않는다 | 보유 키 |
|---|---|---|---|
| **Hermes**(uid 10000, 별도 이미지) | 대화 창, dispatch fan-out·결과 종합, 조회·브리핑·상신, GLOBAL_KILL, **workflow 계획 제출·취소·조회·서술(폴링)** | 승인 결정, 스케줄 변경(P09 위임 범위 밖), 실행, 거래소 접근, **상태 기록, 알림 전달 보장** | OpenRouter, Hermes 봇 토큰, 허용 사용자 |
| **thomas-operator** | 관제봇 폴링, `/approve`·`/resume`·비상 콘솔, 승인 요청 푸시(+Hermes 창 미러링), 프론트데스크 fallback, **workflow 핵심 이벤트 푸시(P08, `deliveries`의 유일 writer)** | 라이브 변수, 문 클라이언트, workflow 테이블 쓰기(`deliveries` 제외) | 관제봇 토큰, 모델 키 |
| **thomas-scheduler / -maint** | risk·maintenance 레인, 크립토 사이클·팩토리·리포트·정리·콘텐츠 아이디에이션, (P09) 일정 변경 검증 함수 공유 | Hermes 호출, 승인, workflow DB 쓰기 | 거래소·라이브(risk만), 스케줄러 발신 봇 토큰 |
| **thomas-pipeline-worker** | dispatch·job 실행, 검증·revise, workspace 쓰기, **attempt 프레임 실행·attempt_id·trace_id 에코·origin `WORKFLOW` 행** | 소켓을 `bridge/`에 노출, **workflow DB 열기** | 모델·검색·네이버 |
| **문 4개** | 프레임 검증·게이트·렌더. **dispatch-bridge: Manager 루프(v3 접수·claim·dispatch·복구), workflow 테이블의 유일 writer. read-bridge: workflow 조회 verb(같은 UID·RW 마운트 안 `mode=ro`)** | 키 보유 | 없음 |

## 3. 결정 표

### 3.0 결정 표 (2026-09-03, V0.1) — 유지. V0.2가 갱신한 행은 3.1에 적었다.

| 항목 | 결정 |
|---|---|
| 승인 창 | 관제봇 유지 + Hermes 창 알림 미러링 |
| 정책 파일 편집 | 조문은 함께 작성, 변경·업로드는 Thomas 직접(살아있는 승인 0건인 시점에 원자 범프) |
| assistant 런 `task_registry` 기록 | 기록(origin `AGENT`) — 완료 |
| 부분정지 | 불요. 규모가 커지면 도메인 부분정지로 전환(기록만) |
| Stop / Run | Stop은 Hermes 권한(자기 판단 가능, 자동 감시 cron은 차단) / Run은 Thomas 확인 후 |
| 취소(cancel) | 1단계는 타임아웃만(런은 완주) — **v2 동기 런에 한해 유지, Q25로 갱신** |
| `development` kind | 닫아 둔다 |
| 정책 sha256 | 경량 도입 — 완료(`policy_fingerprint`) |
| Core 활성화 재발행 | 안 함 |
| 비밀 | Secret Source of Truth 하나 — 완료 |
| 백업 | `hermes backup` 일일 + 루트 5개 tar + 복원 런북 — 완료. **Q28로 확장** |
| API_SERVER_KEY | 끈다 |
| kanban | off. `task_registry`가 유일 원장 — **Q20으로 갱신(workflow ID는 SQLite)** |
| cron | Thomas 스케줄러 유지, Hermes cron은 사람 향한 브리핑·상신만 |
| 필수 체크 | pytest(ubuntu·windows) + Docker compose smoke — 완료(5개 required) |
| 재기동·메모리 | 완료 |
| 예산 | Hermes 런당·일일 카운터 + Thomas `cost_used` — **Q24로 갱신** |
| Compose · Runtime | compose 1개, 런타임은 합치지 않는다 |
| 멱등 | `request_id`는 유일. 재전송 시 기존 런의 `{task_id, status, result}` 반환 — v2 유지, **v3는 Q20** |

### 3.1 결정 표 (2026-09-14, V0.2)

| # | 항목 | 결정 | 근거·조건 |
|---|---|---|---|
| Q18 | Workflow Manager 배치 | **dispatch-bridge 프로세스 안의 루프**(`--workflow-manager` 플래그, `pipeline_worker_cli --revise`와 같은 "배포 결정은 compose 명령 줄의 diff" 관행). 새 UID·소켓·볼륨·컨테이너·healthcheck 없음 | 분리 컨테이너는 **P06 종료 시** 세 기준으로 재결정: 루프 때문에 dispatch 문이 `BRIDGE_BUSY`를 내는가, 루프 장애가 v2 접수까지 막는가, 워커 침해 시 DB 보호가 실제 요구인가. 워커는 이미 governance state 전체(0700, uid 10001)를 쓸 수 있어 별도 UID의 Manager도 워커 침해에서 DB를 지키지 못한다 |
| Q19 | 조정 저장소 | **SQLite** `.runtime_governance_state/workflow/workflow.db`(WAL). writer는 **테이블 단위**로 하나 — Manager 루프(workflows·plan_versions·steps·dependencies·attempts·requests·events·budget_reservations), Operator(`deliveries`) | `bridge_idempotency.py:24-26`이 claim→effect 사이 crash window를 스스로 인정하고, JSONL+filelock으로는 접수·멱등·예산 예약·초기 단계를 한 트랜잭션에 묶을 수 없다. **읽기 규칙:** 같은 UID·RW 마운트 안에서만 `mode=ro`; 읽기 전용 마운트·타 UID에서 DB 파일을 열지 않는다(`-shm` 쓰기 실패). 그 밖의 소비자는 문의 조회 명령 |
| Q20 | 소유권 | workflow ID는 별도 이름공간이고 상태는 SQLite만 소유한다. attempt마다 registry에 origin `WORKFLOW` 행 1개(워커가 열고 닫는 attempt 수준 표시용). `ownership` 테이블·legacy 복사 이전 **없음** | "Reuse first"(CLAUDE.md)와 09-13 원칙("다른 이름의 작업 원장을 병렬로 만들지 않는다")을 **의도적으로 뒤집는** 결정이므로 §3.2의 권위 표로 기록한다. 소유권 축은 이미 있는 `origin`(`task_registry.py:481-516` "origins가 이 함수의 정확성 전부") |
| Q21 | `effect_class` | capability 정의에 `none`/`external`. 1차 4종은 모두 `none`(모델 호출·검색·workspace 쓰기 — 이중 실행의 피해는 비용). `external`(승인 소비·게시·주문)은 1차 workflow가 실행하지 않는다 | 요청이 이 필드를 바꾸지 못한다(closed schema). `none`: deadline 만료 + 예산 잔여 → 새 attempt. `external`: `NEEDS_RECONCILIATION`, 무조건 재시도 금지 |
| Q22 | lease·fence | **heartbeat 채널 없음.** 워커 소켓은 연결당 요청 하나·deadline(`dispatch_bridge.py:133` 600초 재사용)이므로 **연결 수명이 lease, deadline이 만료**. fence는 `attempt_id` 에코 — 현재 attempt일 때만 결과 채택, 늦은 결과는 거부하고 `events`에 대조 기록 | 완료한 attempt를 다시 `RUNNING`으로 돌리지 않는다. 같은 단계의 선택된 성공 시도는 하나 |
| Q23 | 알림 소유자 | 핵심 이벤트 푸시 = **Operator**(관제봇 토큰 보유, Hermes 장애에서 생존), `deliveries`에 상태 기록. 서술 = **Hermes**(`workflow.events` 폴링, Hermes cron 포함). 같은 이벤트 푸시 주체는 하나. **P08 구현(2026-09-14):** 푸시 대상은 COMPLETED·FAILED·BLOCKED·CANCELLED·WAITING_REPLAN; WAITING_APPROVAL은 승인 요청 공지(`announce_pending_approvals`)가 그 푸시이므로 따로 밀지 않는다. 첫 패스는 backlog를 채택하고 보내지 않으며, PENDING은 한 번 재시도, UNCERTAIN은 보존·재전송 없음(at-least-once) | Manager는 설계상 봇 토큰이 없고 Hermes는 푸시 경로가 없다. 상태가 그대로면 무음(A19) |
| Q24 | 예산 두 층 | **강제**: Thomas 측 호출(worker·validator·검색)의 호출 수·토큰 예약(`budget_reservations`). **보고**: Hermes 자체 사용량(Hermes state.db `session_model_usage`; uid 10000 전용) — submit에 동봉하거나 일일 스냅샷을 읽어 표시. **P08 결정(2026-09-14): 스냅샷 읽기** — shim `report_workflow_usage`가 자기 컨테이너의 `session_model_usage`를 세션 단위(정확) 또는 시각 이후(상한)로 합산해 `workflow.report_usage`로 보내고, 저장소 `reported_usage`에 워크플로당 한 행(최신 우선), `workflow.status`의 `budget.reported`에 강제 아님으로 표시 | 예약 단위는 USD가 아니다 — `budgets.recorded_usage_budget`가 무료 tier에서 비용을 계측하지 않는다(`dispatch_spend.py` 서두). 미확인 사용량은 0으로 환급하지 않는다(A12). 거래 예산과 섞지 않는다 |
| Q25 | 취소 2단계 | workflow attempt: `CANCEL_REQUESTED` → 워커의 단계 경계 확인 → `CANCELLED`. 취소 확인 전에 `CANCELLED`를 표시하지 않는다(A11). v2 동기 런은 타임아웃만(V0.1 그대로) | P06 |
| Q26 | 일정 위임(조건부) | §1.3 개정. P09에서만 시행, 정책 범프 동반 | A20·A21 |
| Q27 | 검증 레인 | compose·Docker가 필요한 인수 시험(A26·A28·A30)은 `.github/workflows/docker-image.yml` 스텝(Linux). in-process 부하(A27)는 pytest(양 OS). required 5개 유지 | pytest 레인은 windows-latest에서도 돌므로 compose 시험을 `tests/`에 넣으면 skip이 불가피하고 "핵심 인수 시험 skip 불인정"과 충돌 |
| Q28 | 백업 | `harness_backup.sh`가 tar 전에 `docker exec -u 10001 thomas-dispatch-bridge python -m runtime.mvp_runtime.workflow_cli snapshot`(sqlite backup API)을 만들고 live `workflow/*.db*`를 exclude, 로그에 `workflow-snapshot=ok/FAILED`; `backup_watch.sh`가 검사 | Hermes `hermes backup --quick` 스텝(`harness_backup.sh:48-53`)과 같은 모양. WAL DB의 mid-write tar는 백업이 아니다(런북 §1) |

### 3.2 개념별 권위 표 (Q20의 기록)

| 개념 | 기존 소유자 | workflow ID의 권위 | 기존 소유자의 행 |
|---|---|---|---|
| 요청 멱등 | `bridge_idempotency`(JSONL, claim 15분·완료 24시간, `:65,71`) | SQLite `requests`(v3만) | 없음 — v3 요청은 `bridge_idempotency`에 claim하지 않는다 |
| 작업 상태 | `task_registry`(`task_registry_entry.v0.2`) | SQLite `workflows`/`steps` | attempt마다 origin `WORKFLOW` 행 1개(워커가 열고 닫음). 단계·workflow 상태는 registry에 없다 |
| 실행 시도 | 없음(registry 행이 곧 실행) | SQLite `attempts` | 위 `WORKFLOW` 행이 `attempt_id`를 가진다 |
| 조정 이벤트 | `events.py`(자체 해시 standalone 이벤트) | SQLite `events` — 감사가 아니고 해시 체인이 아니다 | 감사는 pipeline의 audit 체인이 그대로 기록한다 |
| 예산 | `budgets.py`(execution_budget), `dispatch_spend` | 단계별 execution_budget은 기존 그대로. SQLite `budget_reservations`는 workflow 합산 상한(호출 수·토큰) | `dispatch_spend`는 `WORKFLOW` 행도 그대로 집계한다 |
| 승인 | `approval_store`, `consumption` | 변경 없음 | Manager는 `approval_id`를 참조만 한다 |
| 알림 전달 | Operator 관제봇(상태 없음) | SQLite `deliveries` — Operator가 유일 writer | 없음 |
| 스키마 | `schemas/`(런타임 로컬) | `workflow_plan.v0.1`, `workflow_event.v0.1`, `task_registry_entry.v0.3` | `task_registry_entry.v0.2`는 Core 매니페스트·I0.4 인덱스 어디에도 없으므로 런타임 로컬 스키마 추가는 Core 릴리스를 요구하지 않는다 |

## 4. 문 계약

### 4.1 v2 — 완료 (2026-09-04, [`DOOR_API_V2_DESIGN_V0.1.md`](runtime-contracts/DOOR_API_V2_DESIGN_V0.1.md))

typed/versioned frame(PR7), registry `AGENT`(PR8), `request_id` 멱등 재생(PR9), read 조회 verb 4개(PR10), 승인 알림 미러링(PR11). Hermes shim v2는 호스트에 살아 있다(`/root/hermes-trial/data/mcp/`, 시퀀스 2의 P02가 저장소로 옮긴다).

**V0.1 §4 "정정됨" 항목의 남은 stale 한 곳:** `dispatch_bridge.py:131-132`의 주석("the worker writes no task-registry entry … a missed reply is a lost report")은 PR8 이후 거짓이고 같은 파일의 `_replay_data`(registry에서 status를 읽음)와 모순이다. P01에서 정정한다.

### 4.2 v3 — workflow 명령 (P05~P09에서 구현)

`capabilities` 조회로 지원 명령·schema를 확인한다. 서버를 먼저 배포한 뒤 shim을 전환하며, 지원되지 않는 버전에서 조용히 구형 동작으로 떨어져 새 작업을 중복 실행하지 않는다(A18).

| 명령 | 요청·응답의 핵심 | PR |
|---|---|---|
| `workflow.submit` | `request_id`, plan, budget → `workflow_id`, status, `accepted_at`. commit 전에는 accepted를 응답하거나 워커를 호출하지 않는다 | P05 |
| `workflow.status` / `workflow.result` | ID → 단계·사유·출처·`as_of`·artifact 참조 | P05 |
| `workflow.events` | `after_cursor`, `limit` → 변경 이벤트와 `next_cursor` | P08 |
| `workflow.cancel` | ID, `expected_version`, reason → 실제 상태. 즉시 취소 완료 가정 금지 | P06 |
| `workflow.retry_step` | `step_id`, `expected_version`, reason → 정책 검사 후 새 attempt | P06 |
| `workflow.propose_update` | `plan_version`, diff, reason → 검증 결과 또는 승인 대기 | P07 |
| `schedule.propose_change` | 일정 ID/종류/빈도/예산 차이 → 허용 여부·변경안 | P09(조건부) |

`client_id`는 출처 표기에만 쓴다. principal은 소켓 peer와 기존 인증 경로에서 정하며 JSON의 값으로 정하지 않는다. 같은 principal의 `request_id`가 같은 입력이면 기존 작업을 반환하고 다른 입력이면 충돌이다. 삭제된 작업의 재전송은 명시적 expired 또는 tombstone이며 같은 ID로 신규 실행하지 않는다.

### 4.3 상태 전이

- **Workflow:** `RECEIVED → VALIDATED → RUNNING → COMPLETED`. 진행 중 `WAITING_APPROVAL`, `WAITING_REPLAN`, `CANCELLING`. 종결 `FAILED` / `BLOCKED` / `CANCELLED`.
- **Step:** `PENDING → READY → RUNNING → SUCCEEDED`. 실패 유형에 따라 `RETRY_WAIT`, `WAITING_APPROVAL`, `FAILED`, `BLOCKED`. `NEEDS_RECONCILIATION`은 `effect_class=external`에서만. 취소는 `CANCEL_REQUESTED` 뒤 실제 중단 확인을 거쳐 `CANCELLED`. **P06 구현 정정:** `FAILED`·`BLOCKED`는 종결이 아니라 *정착(settled)* 상태다 — 자동 정책이 소진된 것이며 결정(`workflow.retry_step`, P07의 계획 변경)만이 새 attempt를 연다. 종결은 `SUCCEEDED`·`CANCELLED`뿐이고, 하드 캡 3회에 닿은 `FAILED`만 workflow를 `FAILED`로 끝낸다. 그 전까지 workflow는 `WAITING_REPLAN`에서 결정을 기다린다.
- **Attempt:** 새 attempt만 재시도다. fence = `attempt_id`. 워커는 결과 생성 후 기존 pipeline의 감사·결과 저장을 먼저 완료하고 `attempt_id`·`trace_id`·참조를 응답한다. Manager는 fence와 전이 조건을 확인한 트랜잭션에서 step과 이벤트를 갱신한다. 감사 저장과 SQLite는 한 트랜잭션이 아니므로, 응답 유실(브리지 재기동·연결 끊김) 시 `trace_id`·`attempt_id`로 감사 원장을 대조해 완료시키고 모델을 재호출하지 않는다(A10). 감사 저장 실패는 기존 규칙대로 성공 전달하지 않는다(A09).
- **전달:** workflow 완료와 메시지 전달은 구분한다. `deliveries`의 `PENDING/CONFIRMED/UNCERTAIN`. 외부 알림까지 exactly-once라고 약속하지 않는다.
- **기본 상한(설계 시작점, 부하 시험으로 확정):** worker 동시 2(`pipeline_worker.py:164`), workflow 단계 10, 서버 전체 미실행 step 20. revise 1회 유지, Manager 재시도·Hermes 재계획을 합친 총 호출·토큰 예산을 별도로 둔다.

### 4.4 저장소

| 테이블 | 핵심 필드·제약 | writer |
|---|---|---|
| workflows | workflow_id PK, owner, plan_version, status, created_at, updated_at | Manager |
| plan_versions | workflow_id + version UNIQUE, plan_hash, validated_plan, reason | Manager |
| steps | step_id PK, workflow_id, capability, effect_class, input_refs, status, result_ref, row_version | Manager |
| dependencies | step_id + depends_on UNIQUE. 순환·없는 단계 거부 | Manager |
| attempts | attempt_id PK, step_id, attempt_number UNIQUE, deadline_at, status, trace_id, registry_entry_id | Manager |
| requests | principal + request_id UNIQUE, fingerprint, workflow_id, 결과 포인터 | Manager |
| events | 증가 cursor PK, workflow/step/attempt, transition, reason, timestamp | Manager |
| budget_reservations | workflow/step/attempt별 예약·확정·미확인 사용량(호출 수·토큰) | Manager |
| deliveries | channel + event_cursor UNIQUE, status, attempted_at | Operator |

임의 SQL 접근은 제공하지 않는다. 입력은 closed schema로 검증하며 외부 데이터가 capability·actor·권한·effect_class를 덮어쓰지 못한다. 쓰기 트랜잭션은 짧게, 모델 호출 중에는 트랜잭션을 열어 두지 않는다. `busy_timeout`을 둔다.

## 5. 실행 순서

### 5.1 시퀀스 1 (2026-09-03) — 완료

PR1 문서 → PR2 비밀 경계 → PR3 CI 게이트 → PR4 백업·재기동·헬스 → PR5 하네스 통합 → 문 API v2(PR7~PR11) → 정책 1.5.0 → Hermes 쪽 SOUL/shim/cron. 기록은 [`REMAINING_WORK.md`](REMAINING_WORK.md) §K.

### 5.2 시퀀스 2 (2026-09-14) — P00~P11

각 행은 검토 단위이며 크면 세부 PR로 나눈다. 임계 경로는 P03→P04→P05→P06. P02는 독립이라 P03과 병행한다.

| ID | 변경 단위 | 선행 | 종료 조건 |
|---|---|---|---|
| P00 | 이 문서(V0.2), REMAINING_WORK §K.2 | 없음 | 개정 불변식마다 강제 테스트 이름이 적힘. §7 재검증 명령이 `a789bec`에서 통과(A31) |
| P01 | 기준선·기존 결함 분리 | P00 | 번역 renderer가 `translated_text`를 출력, `dispatch_bridge.py:131` 주석 정정, 실행 이미지↔main 3파일 차이 기록, 격리 검증 환경 |
| P02 | `integrations/hermes/` — 4 shim·door client·기존 shim 테스트·설정 템플릿·호환 manifest, read shim의 `data` 통과 | P01 | v2 회귀, stale/미확인 표시(A17·A18). shim 테스트의 CI 레인 결정 |
| P03 | `workflow.py`·`workflow_store.py`·schema 2개·`task_registry_entry.v0.3`(origin `WORKFLOW`) | P00 | DAG·전이·effect_class·원자 접수·멱등·예산 예약·WAL 읽기 규칙·snapshot(A01~A06, A12, A23) |
| P04 | dispatch-bridge 안의 Manager 루프, `--workflow-manager`, `workflow_cli` | P03 | 플래그 off 시 v2 바이트 동일, 루프 장애 독립, Hermes 마운트에 DB 경로 없음(A14, A26) |
| P05 | v3 비동기 단일 작업·워커 attempt 프레임·`WORKFLOW` 행 | P02/P04 | submit→실행→감사→result, attempt_id 에코, v2/legacy 호환, 저장 실패 시 미실행(A03, A04, A09, A18, A29) |
| P06 | 재시도·대사·취소·복구 | P05 | fence, effect_class별 만료 처리, 응답 유실 대조, 취소 2단계(A06~A08, A10, A11, A28). **분리 컨테이너 재결정 기록** |
| P07 | 복합 workflow·계획 수정·승인 대기 | P06 | 조사→초안 3개→검토, 의존 실패·부분 재실행·승인 위조/만료/재사용 차단(A13, A15, A16) |
| P08 | 이벤트 cursor·Operator 푸시·Hermes 서술·예산 두 층 | P07 | A12, A19 |
| P09 | 조건부 일정 위임(§1.3), 정책 범프 | P07/P08 | A20, A21. 정책·테스트·API 한 PR |
| P10 | 백업 스냅샷·복원·진입점 전환 런북·rollback 리허설 | P04 이후, P08까지 보완 | A22, A24, A25 |
| P11 | 제한된 운영 전환·구경로 정리 | P05~P10 | 별도 배포 결정. 대상 진입점별 legacy writer 종료 |

**첫 이정표(P00~P06)의 완료 기준**은 "세션 종료 후 조회"가 아니다 — 그것은 v2 `request_id` 재생(24시간)과 `AGENT` 행으로 오늘도 대부분 된다. 기준은 (1) 기록 성공이 실행의 전제, (2) attempt fence, (3) 실행 중 취소, (4) 결과 대사, (5) 280초 동기 대기 제거를 A03/A04/A07/A10/A11로 입증하는 것이다.

## 6. 비목표와 전환 조건

- **도메인 부분정지** — 지금 없음(V0.1 그대로). 전환 조건: 크립토 정지 중에도 콘텐츠·리서치 레인을 계속 돌려야 하는 요구, 또는 dispatch 동시 런이 문 슬롯(2)을 상시 초과.
- **취소(abort)** — v2 동기 런은 없음. workflow attempt는 P06부터 단계 경계 협력 취소(Q25). registry `RUNNING→CANCELLED`는 `WORKFLOW` 행에 한해 P06에서 허용을 검토한다(지금은 불법 전이).
- **분리 컨테이너·전용 UID** — 지금 없음(Q18). 전환 조건은 Q18의 세 기준이며 P06 종료 시 측정값으로 판단한다. 전환하면 Dockerfile 사용자(`Dockerfile:49`는 10001 하나), 워커 peer 리터럴(`docker-compose.yml:566-570`; `socket_door.resolve_client_uids`는 쉼표 목록 지원), 새 상태 루트 해석기(`store.py:50`·`control.py:44`는 `.runtime_governance_state` 상대 경로 고정), `state_guard`, 백업 멤버, healthcheck가 함께 바뀌고 이 문서의 V0.3이 필요하다.
- **Hermes 사용량 강제** — 없음(Q24). Thomas가 Hermes의 모델 호출을 셀 수 없기 때문이다. 보고만 한다.
- **legacy 기록의 SQLite 복사 이전** — 없음(Q20). 과거 기록은 읽기 가능한 증거로 남는다.
- **`development` kind** — 닫힘(V0.1 그대로).
- **Hermes API 서버·kanban·relay** — 끔(V0.1 그대로).
- **호스트 판독 cron의 Hermes 흡수** — 안 함(V0.1 그대로).

## 7. 재검증 명령

`a789bec`에서 실행한 기대값을 주석에 적었다. V0.1의 "`task_registry` 참조 0"과 "schedule 문자열 없음"은 PR8·PR10 이후 틀린 기대값이었으므로 고쳤다.

```bash
# 불변식 2·8 — 승인 결정의 유일 호출자, enable은 항상 ask
grep -n 'apply_command(' runtime/mvp_runtime/*.py | grep approval          # approval.py의 정의 1줄 + operator.py 호출 1줄
grep -nE 'RUNTIME_GOVERNANCE: ' governance/GOVERNANCE_POLICY.yaml           # RUNTIME_GOVERNANCE: 15
python -m pytest tests/test_mvp_runtime_switch_bridge.py -q -k "cannot_be_named or resume_is_absent"
# 불변식 3 — 문에 schedule 변경 verb 없음 (조회 verb schedules·scheduler_events는 있다)
python -m pytest tests/test_mvp_runtime_read_bridge.py -q -k "no_mutating or no_control_verb or mutating_and_unknown"
grep -nE '"(add|remove)"|schedule' runtime/mvp_runtime/switch_bridge.py                      # 스케줄 verb 없음 (enable/disable은 그 문의 스위치 verb)
grep -nE '"(add|enable|disable|remove)"' runtime/mvp_runtime/read_bridge.py                    # 없음
# 불변식 4 — 실행은 워커·레인, kind는 4종. 워커는 AGENT 행을 쓴다(PR8) — 0이 아니다
grep -c 'task_registry' runtime/mvp_runtime/pipeline_worker.py             # 12 (0이면 PR8이 되돌려진 것)
grep -n '^_ALLOWED_KINDS' runtime/mvp_runtime/dispatch_bridge.py             # 4종 (analysis, research, translation, content)
grep -nE '^WORKER_ORIGINS|^AGENT_ORIGIN' runtime/mvp_runtime/task_registry.py   # WORKER_ORIGINS = {AGENT}; P03 이후에도 WORKFLOW는 여기 없어야 한다
python -m pytest tests/test_mvp_runtime_task_registry.py -q -k "ownership_sets or origin_enum or only_the_worker_set"
# 불변식 5·7 — depends_on 없음, 문 env 정확집합, 라이브 변수는 scheduler만, hermes는 bridge/만 마운트
python -m pytest tests/test_deployment_env_passthrough.py -q
# 불변식 5·6·7 (호스트) — 접점은 bridge/ 하나, GroupAdd 없음, uid 게이트
docker inspect hermes --format '{{json .HostConfig.Binds}} {{json .HostConfig.GroupAdd}}'   # data + bridge, null
ls -ln /root/thomas_agent/.runtime_governance_state/bridge                  # srw-rw---- 10001 10000 ×4
for c in read switch dispatch knowledge; do docker exec thomas-$c-bridge printenv MVP_BRIDGE_CLIENT_UID; done   # 10000 ×4
docker exec thomas-pipeline-worker printenv MVP_BRIDGE_CLIENT_UID           # 10001
```
