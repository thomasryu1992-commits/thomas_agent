# Hermes 오케스트레이터 아키텍처 V0.1 — 결정 기록과 불변식

**Status:** 결정됨 (Thomas, 2026-09-03) · 구현 전. 이 문서는 통합 시퀀스(§5)의 입력이다.
**Normative authority:** None — 권한의 정본은 [`governance/GOVERNANCE_POLICY.yaml`](../governance/GOVERNANCE_POLICY.yaml)이고, 불변식은 코드·테스트·정책이 강제한다. 이 문서는 그것들에 이름을 붙이고, 통합이 무엇을 보존해야 하는지를 적는다.
**Baseline:** `origin/main` `ab9ec51`(2026-09-03), hermes-agent `9accf79`(2026-07-30 빌드 고정), 호스트 실측 2026-09-03. 아래 `path:line`은 모두 이 커밋 기준이다.
**근거:** 사전 분석(호스트 볼트 `ops/unification-analysis-2026-09-03.md` — 판독기 19, 주장 76건 코드 반증, HELD 49 / 정정 27)과 결정 기록(`ops/unification-decisions-2026-09-03.md`). 둘 다 레포 밖이므로 이 문서가 레포 안의 정본이다.

## 0. 한 화면

```text
         Thomas
           │
           ▼
   ┌──────────────┐
   │    Hermes    │
   │ Orchestrator │
   └──────┬───────┘
          │
  Typed / Versioned Door
          │
   ┌──────┴───────────────┐
   │                      │
   ▼                      ▼
Thomas Control Plane   Read / Dispatch API
   │
 ┌─┼──────────┐
 ▼ ▼          ▼
Scheduler  Workers  Content/Research
 │ autonomous
 ▼
Crypto / Maintenance / Scheduled Jobs

        별도 권한 Plane
               │
               ▼
        thomas-operator
   Approval / Resume / Emergency
```

코드 대응:

| 도식 | 지금의 코드 | 통합 후 |
|---|---|---|
| Typed / Versioned Door | `runtime/mvp_runtime/socket_door.py` — 개행 JSON 1왕복, 8KiB 프레임, 미지 키 거부(read 제외), 신원·버전 필드 없음 | 프레임에 `data`(구조화)·`client_id`(귀속)·`proto`(버전) 추가. 미지 키 거부 규칙 때문에 서버 선배포 |
| Thomas Control Plane(Hermes가 닿는 부분) | `switch_bridge.py` — `disable`(무승인·즉시·전역), `enable`(항상 승인) | 그대로 + read 문 **조회** verb(`schedules`·`scheduler_events`·`heartbeat`·`approval_status`). 스케줄 **변경** verb는 만들지 않는다 |
| Read / Dispatch API | `read_bridge.py`(9 verb, 콘솔 텍스트), `dispatch_bridge.py`(kind 4종, P3, 동기), `knowledge_bridge.py`(3 verb) | dispatch에 `task_registry` 기록(origin `AGENT`)·`request_id` 멱등·2단계 비동기 핸들 |
| Scheduler (autonomous) | `scheduler.py` 두 레인(risk·maintenance), `schedules.jsonl`, 컨테이너 내 `scheduler_cli`만이 행을 바꾼다 | 변경 없음 |
| Workers | `pipeline_worker.py` — `internal/pipeline.sock`, peer uid 10001만 | 변경 없음(registry 기록 제외) |
| 별도 권한 Plane | `thomas-operator` — 관제봇 폴링, `/approve`·`/resume`·`/kill`·`/pause`, 승인 요청 자동 푸시 | 그대로 + 승인 요청 **알림**을 Hermes 봇 창에도 미러링(결정은 여전히 관제봇) |

## 1. 불변식 8 — 무엇이 지키고 있고, 통합이 무엇을 보존하는가

강제 주체: **code** / **test**(pytest 정확집합) / **policy** / **ops**(배포 체크리스트·실측). "구조적"은 코드 한 줄이 아니라 배치가 보장하는 것.

### 1. Hermes = orchestrator

Hermes는 Thomas의 요청을 나누고(dispatch), 결과를 종합하고, 스스로 결정할 수 없는 것을 [상신] 형식으로 가져온다. 이 역할은 레포 밖(`/root/hermes-trial/data/SOUL.md`, `skills/thomas-ops`)에서 정의되며 여기서는 **나머지 일곱이 그 역할의 경계**라는 점만 적는다. Hermes가 "무엇을 할 수 있는가"는 아래 일곱이 "무엇을 할 수 없는가"로 정의된다.

### 2. Hermes ≠ approval authority

- 승인 결정(`APPROVED`)의 프로덕션 유일 호출자는 `operator.py:383`(`approval.apply_command`)이고, 그 앞에 `verify_control_channel`(`operator.py:230-241` — private·비전달·등록 user·등록 chat)이 선다. **code**
- 어시스턴트가 운반한 승인은 `invalid_approval_sources(other_user, ambiguous_expression)`로 기각된다. **policy** `governance/GOVERNANCE_POLICY.yaml:222-239`; 기각 이력은 [`proposals/HERMES_AGENT_SWITCH_V0.1.md`](proposals/HERMES_AGENT_SWITCH_V0.1.md) §8.
- switch `enable`은 게이트 상태와 무관하게 **항상** `APPROVAL_REQUIRED`이며 caller는 `CMD_RESUME`를 이름으로 부를 수 없다. **test** `tests/test_mvp_runtime_switch_bridge.py:160-174`.
- 정직하게 적어 둘 한계: `Verification.method`는 서명 없는 문자열이고 검증은 문자열 일치뿐이다(`approval.py:355-362`). 유일성은 **정책과 호출자 부재**가 지키는 것이지 암호학적 강제가 아니다. 그래서 통합 시 "Hermes가 승인을 기록한다"는 어떤 코드도 **정책 위반**으로 다룬다.
- 결정 Q1-b: 승인 요청 **알림**은 Hermes 봇 창에도 미러링한다. 알림은 결정이 아니다 — `/approve`는 관제봇에서만 유효하고, Hermes 창의 `/approve`는 Hermes 자체 승인 큐로 가서 증발한다.

### 3. Hermes ≠ scheduler authority

- 스케줄 행의 CRUD는 컨테이너 안 `scheduler_cli add/enable/disable/remove`뿐이다(`scheduler_cli.py:221-245,281-328`). 문 4개 어디에도 schedule verb가 없다(`read_bridge.py:57-70`, `switch_bridge.py:105-107`). **code**
- 스케줄러는 Hermes를 호출하지 않는다. 역방향 접점 0. **구조적**
- 통합 규칙: read 문에 **조회** verb만 추가한다. enable/disable/remove는 어느 문에도 만들지 않는다(결정 Q4·Q13). "read 문에 상태 변경 verb 없음"은 `read_bridge.py` 서두의 계약이며 조회 verb 추가는 정확집합 테스트를 깨지 않는다.

### 4. Hermes ≠ executor

- 실행은 `pipeline_worker.py`(uid 10001)와 두 스케줄러 레인이 한다. Hermes가 낼 수 있는 것은 dispatch 문의 kind 4종(`analysis/research/translation/content`, P3)뿐이며 `development`는 의도적으로 제외돼 있다(`dispatch_bridge.py:86-93`). **test** `tests/test_mvp_runtime_dispatch_bridge.py:73-92`.
- 워커 소켓은 `internal/`에 있고 `bridge/` 경로를 거부하며 peer uid 10001만 받는다 — Hermes 마운트로는 도달 불가. **test** `tests/test_mvp_runtime_pipeline_worker.py:208-244`.
- 문을 통한 모든 효과는 상수 actor `assistant_bridge`로 기록되고 intake에는 `requester_type=agent`로만 들어간다(`pipeline_worker.py:127-145`). 역할 레지스트리에 assistant/orchestrator 슬롯은 없고 Hermes는 requester 축이다. 통합 후에도 그렇다.

### 5. Scheduler survives Hermes failure

- 두 compose 프로젝트로 분리돼 있어 hermes 재생성이 Thomas 8개에 닿지 않고, 그 반대도 같다(2026-08-29 Thomas 8개 재생성 중 hermes 무중단 실측). **ops**
- 스케줄러는 Hermes의 존재를 모른다 — 코드 참조 0, 네트워크 의존 0(문은 unix socket), `depends_on` 없음. **구조적**
- 통합 규칙(PR5): compose 프로젝트를 하나로 합쳐도 **어떤 Thomas 서비스도 hermes를 `depends_on`하지 않고, hermes 헬스가 Thomas 재시작 정책에 들어가지 않는다.** 이것을 compose 테스트로 핀한다(`tests/test_deployment_env_passthrough.py`가 서비스 집합을 열거하는 방식과 같은 자리).

### 6. Operator survives Hermes failure

- operator는 관제봇 토큰(`docker-compose.yml:104`)으로 독립 폴링한다. Hermes 봇은 다른 봇이고, 한 토큰에 폴러는 하나뿐이라 두 봇은 구조적으로 합쳐질 수 없다(`docker-compose.yml:209-219`). **구조적**
- 통합 규칙: 승인 알림 미러링(Q1-b)은 **best-effort**다. Hermes 창 발신 실패는 operator의 승인 처리·관제봇 푸시에 영향을 주면 안 된다.
- 비상 콘솔(`/kill`·`/pause`·`/resume`, `operator.py:346-357`)과 호스트 `console_cli`는 Hermes 없이 동작한다. Hermes가 죽어도 Thomas는 관제봇 한 창으로 멈추고 되살릴 수 있다.

### 7. Hermes cannot read exchange / write secrets

- 문 4개 프로세스의 env는 정확히 `{MVP_BRIDGE_CLIENT_GID, MVP_BRIDGE_CLIENT_UID}`. **test** `tests/test_deployment_env_passthrough.py:285-294`.
- 거래소·라이브 8변수(`MVP_LIVE_TRADING`, `MVP_LIVE_ORDER_API_*`, `BINANCE_ACCOUNT_API_*`, 확인 문구, `MVP_ACCOUNT_FEED`)는 `thomas-scheduler`만 갖는다. **test** `:133-142,385-410,505`. 자금 조회는 스케줄러가 쓴 스냅샷 파일을 read 문이 렌더할 뿐이다.
- Hermes 컨테이너의 env는 3개(OpenRouter 키, Hermes 봇 토큰, 허용 사용자)이고, 상태 디렉터리는 `0700 uid 10001`이며 `bridge/`만 마운트된다. **ops** — 이 경계는 다른 레포(hermes 쪽 compose)가 만들므로 이 레포의 pytest가 관측하지 못한다. [`DEPLOYMENT.md`](DEPLOYMENT.md)의 체크리스트가 소유한다.
- 결정 Q9(PR2): 비밀은 **Secret Source of Truth 하나**에서 서비스별로 **필요한 키만** 투영한다. 지금은 Hermes 봇 토큰이 두 파일에 중복 저장돼 있고(Thomas `.env`의 `SCHEDULER_TELEGRAM_BOT_TOKEN` = hermes.env의 봇 토큰, 해시 대조 확인), Thomas `.env`가 0644다.

### 8. Resume requires Thomas approval

- switch `enable`은 ask를 만들 뿐이고, spend는 `APPROVED`·미만료(RUNTIME_GOVERNANCE 15분, `governance/GOVERNANCE_POLICY.yaml:334`)·지문·scope·`stop_ref` 일치·1회용을 모두 통과해야 `CMD_RESUME`에 닿는다(`switch_bridge.py:380-555`). **code + policy** `governance/GOVERNANCE_POLICY.yaml:297-323`(`assistant_switch`: `enable: approval_required_always`, `caller_cannot_name_resume: true`).
- `runtime` scope 재개는 `trading_armed`를 되살리지 않는다(`switch_bridge.py:176-179`; 설계는 [`runtime-contracts/ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md`](runtime-contracts/ASSISTANT_RESUME_SCOPE_SPLIT_DESIGN_V0.1.md)). 정지는 무장도 끄고, 그 해제는 끈적하다.
- "Thomas approval"의 다른 형태: Thomas 자신이 관제봇에서 치는 `/resume`(무승인·재무장, `operator.py:346-357`)과 호스트 `console_cli`. 이 둘은 Hermes 경로가 아니다.

### 파생 규칙 — Hermes의 stop은 GLOBAL_KILL이다 (결정 Q4·Q5)

```text
Hermes
  │
  └─ GLOBAL_KILL
        ↓
Thomas Control State (operator_control_state.json — 도메인 필드 없음, control.py:81-103)
        ↓
money path / dispatch / 두 스케줄러 레인(claim-and-drop, catch-up 없음) / 승인 소비  전부 중단
```

- Hermes `disable`(kill·pause)은 무승인·즉시이며 `trading_armed=False`를 함께 쓴다(`control.py:646-671`). 도메인별 부분정지는 **없다**(Q4-a). 규모가 커져 크립토 정지 중에도 콘텐츠·리서치 레인을 돌려야 하면 그때 `ControlState`에 domain을 넣는다(§6).
- Stop 권한의 범위(③-a): Hermes가 **자기 판단**으로 정지할 수 있다. 단 **자동 감시 cron은 정지를 누르지 못하게 코드로 막는다**(Hermes 쪽 cron `enabled_toolsets`에서 switch 제외). 오탐 자동 정지의 비용(회차 드롭·재무장 해제·`/resume` 복귀)을 사람 판단 뒤에 둔다.

## 2. 역할 분담

| 주체 | 한다 | 하지 않는다 | 보유 키 |
|---|---|---|---|
| **Hermes**(uid 10000, 별도 이미지) | 대화 창, dispatch fan-out·결과 종합, 조회·브리핑·상신, GLOBAL_KILL | 승인 결정, 스케줄 변경, 실행, 거래소 접근 | OpenRouter, Hermes 봇 토큰, 허용 사용자 |
| **thomas-operator** | 관제봇 폴링, `/approve`·`/resume`·비상 콘솔, 승인 요청 푸시(+Hermes 창 미러링), 프론트데스크 fallback | 라이브 변수, 문 클라이언트 | 관제봇 토큰, 모델 키 |
| **thomas-scheduler / -maint** | risk·maintenance 레인, 크립토 사이클·팩토리·리포트·정리·콘텐츠 아이디에이션 | Hermes 호출, 승인 | 거래소·라이브(risk만), 스케줄러 발신 봇 토큰 |
| **thomas-pipeline-worker** | dispatch·job 실행, 검증·revise, workspace 쓰기 | 소켓을 `bridge/`에 노출 | 모델·검색·네이버 |
| **문 4개** | 프레임 검증·게이트·렌더 | 키 보유 | 없음 |

## 3. 결정 표 (2026-09-03)

| 항목 | 결정 |
|---|---|
| 승인 창 | 관제봇 유지 + Hermes 창 알림 미러링 |
| 정책 파일 편집 | 조문은 함께 작성, 변경·업로드는 Thomas 직접(살아있는 승인 0건인 시점에 원자 범프) |
| assistant 런 `task_registry` 기록 | 기록(origin `AGENT`). 부하 검토 후 — append 락 경합·`/tasks` 렌더 비용·파일 성장률 |
| 부분정지 | 불요. 규모가 커지면 도메인 부분정지로 전환(기록만) |
| Stop / Run | Stop은 Hermes 권한(자기 판단 가능, 자동 감시 cron은 차단) / Run은 Thomas 확인 후 |
| 취소(cancel) | 1단계는 타임아웃만(런은 완주). abort 프레임은 2단계 후보 |
| `development` kind | 닫아 둔다. 추후 스케줄된 주간 감사 용도에 한해 개방 검토 |
| 정책 sha256 | 경량 도입 — 기동 시 해시 기록, 변경 감지 시 알림(fail-closed 아님) |
| Core 활성화 재발행 | 안 함. ci_test 사실을 문서화, 백업 범위에 포함 |
| 비밀 | Secret Source of Truth 하나 → Hermes·Scheduler·Operator에 필요한 키만 투영. `.env` 0600 |
| 백업 | 가동 중 `hermes backup`(sqlite backup API) 일일 + 루트 5개 tar(`.runtime_governance_state`, hermes data, `THOMAS_CORE/{activations,approvals}`, `workspace`, `.env`) + 복원 런북 |
| API_SERVER_KEY | 끈다 |
| kanban | off(`dispatch_in_gateway: false`). `task_registry`가 유일 원장 |
| cron | Thomas 스케줄러 유지, Hermes cron은 사람 향한 브리핑·상신만, 호스트 판독 cron 미흡수 |
| 필수 체크 | pytest(ubuntu·windows) + Docker compose smoke를 required로 |
| 재기동·메모리 | `S6_KILL_GRACETIME=25000` + restart drain timeout; hermes mem_limit 유지 + pipeline-worker·operator에도 mem_limit(스케줄러 2레인은 무제한) |
| 예산 | Hermes 런당·일일 카운터+상한 + Thomas `cost_used` 채우기(`dispatch_spend_watch` 활성) |
| 프론트데스크 · content_ideation | 프론트데스크 유지(관제봇 fallback). content_ideation 결함 3개(`blog_content.py:455,470,491`)는 09-06 첫 발화 전 수정 |
| Compose · Runtime | compose 1개, **런타임은 합치지 않는다** — 이미지 2개, uid 2개, 상태 디렉터리 2개, 코드 공유 0 |
| 멱등 | `request_id`는 유일. 재전송 시 기존 런의 `{task_id, status, result}` 반환(RUNNING이면 status까지) |

## 4. 문 계약의 방향 (구현은 PR5 이후)

지금의 문은 "사람이 읽는 콘솔"이고, 오케스트레이터가 필요로 하는 것이 구조적으로 없다 — 비동기 핸들, 회수 경로, 구조화 응답, 클라이언트 신원, 승인 상태 조회, 스케줄 조회. 방향만 적는다.

1. **Typed / Versioned frame** — `socket_door` 프레임에 `proto`(정수 버전), `client_id`(Hermes 세션·cron·위임 자식 구분; actor 상수는 그대로 두고 task 레코드 `requester_id`에만 닿는다), 응답에 `data`(구조화)를 추가. 콘솔 텍스트는 유지.
2. **registry 기록** — `task_registry.ORIGINS`에 `AGENT`, worker가 ASSISTANT_PROFILE 런만 `record_submission/close_entry`, 응답에 `registry_entry_id`. 그러면 read 문 `tasks/history/result`가 Hermes 런을 본다. 부하 검토 선행.
3. **멱등** — shim이 호출마다 `request_id`(uuid4)를 보내고 재시도에 재사용. 서버는 `bridge_requests` 클레임 → registry 조회 → `{task_id, status[, result]}` 재생. `REQUEST_IN_FLIGHT` 에러는 status 응답으로 대체.
4. **read 조회 verb** — `schedules`, `scheduler_events`(fired/failed/gap/abandoned/deferred), `heartbeat`, `approval_status`. 변경 verb 없음.
5. **승인 알림 미러링** — operator `announce_pending_approvals`에 발신처 1개 추가(best-effort).
6. **2단계** — 비동기 dispatch(submit→id→poll), abort 프레임·CANCELLED 전이.

**정정됨(2026-09-04)** — 아래 오류는 고쳐졌다(레포 주석 3곳·Hermes SKILL.md·shim 메시지). 기록: `dispatch_bridge.py:128,274`와 Hermes 쪽 SKILL.md가 "타임아웃 후 `result <task_id>`로 회수"를 안내하지만, 워커는 registry를 쓰지 않아 그 경로는 존재하지 않는다(`pipeline_worker.py`에 `task_registry` 참조 0건, `registry_console.py:362-376`은 `treg_` id만 매치).

## 5. 실행 순서

코드보다 문서가 먼저다. 각 PR은 앞 PR이 머지된 뒤 시작한다.

| PR | 범위 | 담는 것 | 검증 |
|---|---|---|---|
| **PR1** 이 문서 | `docs/` | 도식, 불변식 8, 결정 표, 순서 | 링크 무결성 게이트, Active Architecture Gate |
| **PR2** 보안·비밀 경계 | `.env`, compose, tests | `.env` 0600 · single secret source · per-service secret injection(env_file 폐지, `environment:` 열거) · **secret ownership matrix**(문서 + `test_deployment_env_passthrough` 정확집합에 hermes 명시) | 정확집합 테스트, `docker compose config`로 서비스별 env 이름 대조 |
| **PR3** CI 게이트 | 브랜치 보호 | `MVP runtime pytest (ubuntu-latest)`·`(windows-latest)`·`Docker build + fail-closed smoke`를 required check로 | `gh api …/branches/main/protection` |
| **PR4** 백업·재기동·헬스 | 스크립트, compose, 런북 | Hermes healthcheck(`state/gateway.heartbeat` 나이) · SQLite backup · 루트 5개 백업 · restore runbook · `S6_KILL_GRACETIME` · restart drain timeout · 로그 회전(`mcp-stderr.log`) | 백업 산출물·복원 리허설, unclean 종료 0건 |
| **PR5** 하네스 통합 | compose | hermes를 9번째 서비스로(`-p thomas_agent`), 런타임 분할 유지, mem_limit 3곳, `depends_on` 없음 테스트 | `docker compose --dry-run`, 문 왕복(uid 10000 통과·uid 0 거부) |
| 이후 | 문 API v2 → 정책 1.5.0(Thomas 적용) → Hermes 쪽 변경 | §4 / 조문·경량 해시 / SOUL·shim·cron·config | — |

통합과 무관하게 지금: `.env` 0600, content_ideation 결함 수정, dispatch 회수 안내 문구 정정.

## 6. 비목표와 전환 조건

- **도메인 부분정지** — 지금 없음. 전환 조건: 크립토 정지 중에도 콘텐츠·리서치 레인을 계속 돌려야 하는 요구, 또는 dispatch 동시 런이 문 슬롯(2)을 상시 초과. 비용: `ControlState`에 domain + 재검사 5곳(dispatch·worker·scheduler·intake·승인 소비).
- **취소(abort)** — 1단계 없음. fan-out 규모가 커지면 worker abort 프레임 + registry `RUNNING→CANCELLED` 허용(지금은 불법 전이).
- **`development` kind** — 닫힘. 스케줄된 주간 감사 1회성 잡에 한해 개방 검토. 열 때 kind 테이블 3중 사본(`planner`·`frontdesk`·`dispatch`) 동시 편집 + 정확집합 테스트 + 정책 role_allowlist + Hermes 쪽 사람 확인 게이트.
- **Hermes API 서버·kanban·relay** — 끔. 켜면 키 보유자가 uid 10000과 등가가 된다.
- **호스트 판독 cron의 Hermes 흡수** — 안 함. 감시 주체가 healthcheck 없는 컨테이너가 되기 때문. Hermes healthcheck(PR4) 뒤 재검토.
- **무음 감시 cron 대체** — read 문 `scheduler_events` verb가 생기면 되먹임으로 대체 검토.

## 7. 재검증 명령

```bash
# 불변식 2·8 — 승인 결정의 유일 호출자, enable은 항상 ask
grep -n 'apply_command(' runtime/mvp_runtime/*.py | grep approval          # operator.py 한 줄
sed -n '328,340p' governance/GOVERNANCE_POLICY.yaml                        # RUNTIME_GOVERNANCE: 15
# 불변식 3 — 문에 schedule verb 없음
grep -n 'schedule' runtime/mvp_runtime/read_bridge.py runtime/mvp_runtime/switch_bridge.py   # verb 없음
# 불변식 4 — 워커는 registry를 모른다, kind는 4종
grep -c 'task_registry' runtime/mvp_runtime/pipeline_worker.py             # 0
sed -n '88,105p' runtime/mvp_runtime/dispatch_bridge.py
# 불변식 7 — 문 env 정확집합, 라이브 변수는 scheduler만
python -m pytest tests/test_deployment_env_passthrough.py -q
# 불변식 5·6·7 (호스트) — 접점은 bridge/ 하나, GroupAdd 없음, uid 게이트
docker inspect hermes --format '{{json .HostConfig.Binds}} {{json .HostConfig.GroupAdd}}'
ls -ln /root/thomas_agent/.runtime_governance_state/bridge
for c in read switch dispatch knowledge; do docker exec thomas-$c-bridge printenv MVP_BRIDGE_CLIENT_UID; done
```
