# 제안: 자격증명 평면 분리 V0.1 — 어시스턴트와 대화하는 프로세스는 키를 들지 않는다

상태: 제안 (Thomas 승인 전). 작성 2026-08-10, 기준 `main` = `7561e1e`.

---

## 0. 한 줄

dispatch 문에서 **엔진을 꺼내** 자격증명 없는 문(검증·킬스위치·멱등·귀속)과 자격증명 있는
워커(파이프라인 실행)로 나눈다. Naver 라이선스가 나오기 **전에** 하면 광고 계정에 서명하는
키가 Hermes와 대화하는 프로세스에서 단 하루도 살지 않는다. 정책 diff는 0이다.

---

## 1. 문제 — 측정된 현재 상태

`docker-compose.yml`의 `environment:` 블록 기준 (2026-08-10):

| 서비스 | 모델 키 | 검색 키 | Naver (광고계정 서명) | Telegram | Binance 계정·주문 | 라이브 게이트 |
|---|---|---|---|---|---|---|
| operator | ✓ | ✓ | — | ✓ (폴링) | — | — |
| **scheduler** | ✓ | ✓ | ✓ | ✓ (발신) | ✓ | ✓ |
| **dispatch-bridge** | ✓ | ✓ | ✓ | — | — | — |
| read / switch / knowledge | — | — | — | — | — | — |

두 줄이 문제다.

**scheduler는 이 배포의 모든 자격증명을 한 주소공간에 모은다.** hosted LLM의 응답을
파싱하는 프로세스가 Binance 주문 서명 비밀을 들고 있고, `MVP_LIVE_TRADING=real`인 오늘
그 `.env`는 비용이 아니라 능력이다 (`test_deployment_env_passthrough.py`의 라이브 표면
주석이 이 반전을 이미 기록했다).

**dispatch-bridge는 문과 엔진이 한 프로세스다.** 이 문은 Hermes가 여는 문이고, Hermes는
신뢰하지 않는 텍스트를 읽는 에이전트다 — 이 저장소가 BRIDGE_ERROR에서 파일 경로를 뺀
바로 그 이유. 그런데 같은 프로세스가 `pipeline.run_task`를 자기 안에서 돌리기 때문에
모델 키가 필요해졌고, #650이 `research`/`content` kind를 이유로 Naver 다섯 변수를
추가하면서 이제 **Hermes 프레임을 파싱하는 코드와 광고 계정에 서명하는 비밀이 같은
주소공간에 있다.** #650 자신이 명시했듯 `NAVER_SEARCHAD_SECRET_KEY`의 read-only는 이
코드의 성질이지 자격증명의 성질이 아니다 — 같은 서명이 광고 spend 엔드포인트에 닿는다.

나머지 세 문(read/switch/knowledge)은 이미 옳은 형태다: 자격증명 env 0개, 문은 권위를
갖지 않고 판정만 나른다. **dispatch만 예외이고, 예외가 된 이유는 설계가 아니라 "파이프라인을
문 안에서 돌린다"는 구현 편의였다.** 이 제안은 그 예외를 없앤다.

(부수 드리프트: dispatch-bridge의 헤더 주석은 아직 "provider key를 두지 않는다"라고
말하는데 아래 env 블록은 넘긴다. 별건으로 수정 중이며 이 제안의 대상은 아니다.)

---

## 2. 왜 지금인가 — 라이선스 전이 싼 순간

세 사실이 겹친다.

1. **`MVP_NAVER_RESEARCH`는 unset이고 검색광고 API 라이선스는 미취득이다** (#650 "Still
   not enabled", 블로그 레인 상신 §7). 값이 없으므로 오늘의 노출은 이론상이다.
2. **`naver_research.py`를 호출하는 코드가 아직 없다.** Phase 1은 자기완결 모듈이고,
   `select_keyword_tool()`은 어느 경로에서도 불리지 않는다. 즉 env를 워커로 옮기는 일은
   **돌아가는 것을 하나도 끊지 않는다** — 순수한 토폴로지 + 테스트 이동이다.
3. **블로그 레인 Phase 2·3(워크플로우, `content_ideation` 스케줄)이 아직 안 지어졌다.**
   지금 정하면 이후 배선이 처음부터 워커를 향한다. 라이선스가 먼저 나오면 이 작업은
   "핫한 레인의 이사"가 되고, 그 순간부터 값이 채워진 spend-capable 키가 Hermes-facing
   프로세스에 산다.

라이선스는 Thomas의 손에 달린 외부 절차라 언제 완료될지 이쪽에서 정할 수 없다. 정할 수
있는 것은 순서뿐이고, 순서의 올바른 쪽은 자명하다: **PR-A·PR-B가 라이선스보다 먼저.**

---

## 3. 목표 그림 — 두 평면

Thomas가 제시한 최종 그림 그대로이며, 용어만 하나 정한다: "Research Worker"는 네 kind
(analysis·research·translation·content) 전부를 돌리는 P3 파이프라인 엔진이므로 서비스
이름은 **`pipeline-worker`** 로 한다 (research.general 역할과의 혼동을 피하기 위해).

```
평면 R (리서치/디스패치)                     평면 M (돈)
─────────────────────────                  ─────────────────────────
[Hermes]                                    scheduler
   │  프레임 (신뢰하지 않는 텍스트)              ├ 시장데이터 · 계정 · 주문 · 라이브 게이트
   ▼                                          ├ crypto cycle → live_route (오늘과 동일,
dispatch 문                                   │   프로세스 홉 0개 추가)
   │  검증·킬스위치·멱등·귀속                    └ 발신 Telegram
   │  자격증명 env 0개
   ▼  검증된 요청, 내부 소켓
pipeline-worker
   모델 키 · 검색 키 · Naver 키
   P3 REVIEW_ONLY 파이프라인 실행
```

Phase ①(이 제안)은 평면 R을 완성하고 scheduler에서 Naver env를 뗀다. 평면 M의 완성
(scheduler가 모델·검색 키까지 내려놓는 것)은 §7의 방향 승인 아래 별도 상신으로 간다.

---

## 4. 불변식

1. **어시스턴트가 닿는 프로세스는 자격증명 env 0개.** read/switch/knowledge가 이미 이
   형태다. 이 제안 후 dispatch도 같아진다.
2. **돈 경로에 새 홉을 만들지 않는다.** 어느 phase도 crypto cycle→주문 경로를 프로세스
   경계로 자르지 않는다. 실거래 grant의 만료를 없앤 논리(만료가 CLOSE를 막아 포지션을
   가둔다)와 같은 논리로, 워커가 죽어도 CLOSE는 영향을 받지 않아야 하며 — 받을 수 없다,
   워커는 돈 경로에 없으므로.
3. **fail-closed, 폴백 금지.** 워커 불달은 typed 거부(`WORKER_UNAVAILABLE`)다. 문 안에서
   `run_task`로 폴백하는 경로는 만들지 않는다 — 폴백은 키를 문에 되돌려 놓는 것이고,
   조용한 성공으로 위장한 원상복구다.
4. **단일 tick 계약 유지.** Phase ①은 스케줄러의 코드를 건드리지 않는다 (env 블록 한
   개 제거뿐). 두 번째 tick 프로세스는 만들지 않는다.
5. **Hermes 계약 불변.** 같은 tool, 같은 동기 요청/응답, 같은 `reason_code` 면, 같은
   귀속(`assistant_bridge`). Hermes 쪽 시임·설정은 한 줄도 바뀌지 않는다.
6. **내부 소켓은 Hermes가 닿을 수 없다.** §5.2 — 이것이 지켜지지 않으면 분리 전체가
   장식이 된다.

---

## 5. 메커니즘 — Phase ①

### 5.1 문과 엔진의 분리

`apply_dispatch`의 검증은 전부 문에 남는다: 닫힌 키 집합, kind 4종 allowlist, reason
요구, 킬스위치 확인, 멱등 claim/release/complete (원장은 공유 마운트이므로 문에서 그대로).
바뀌는 것은 마지막 한 칸이다 — 오늘 `resolve_providers`를 주입받아 `run_task`를 부르는
자리에서, **`forward` callable을 주입받아 검증 통과 요청을 내부 소켓으로 넘기고 응답을
그대로 릴레이한다.**

`pipeline-worker`는 같은 이미지의 새 서비스(uid 10001, state rw + Core ro 마운트)로:

- 내부 소켓을 listen한다 (`socket_door` 재사용 — 프레이밍·데드라인·크기 상한·동시성
  상한·오류 봉투가 기존 문들과 같은 코드).
- 받은 요청을 **재검증**한다 (kind allowlist·킬스위치 재확인 — 싸고, fail-closed 원칙상
  문을 신뢰하지 않는다). 멱등은 문의 것이므로 재수행하지 않는다.
- provider 선택(Safety-Flag Gate, grant를 요청마다 재읽기)을 지금 문이 하는 그대로 자기
  안에서 하고, `run_task`를 부른다. 귀속은 오늘과 동일하게 `assistant_bridge`.

동시성 상한 `MAX_CONCURRENT_REQUESTS = 2`는 문에 남고(입구에서 자원·지출 상한), 워커도
같은 값을 갖는다 — 문이 릴레이 동안 슬롯을 쥐므로 실효 동시성은 오늘과 동일하다.
$50/day assistant_dispatch 지출 알람은 원장의 귀속을 읽으므로 영향이 없다.

### 5.2 내부 소켓의 위치가 안전성의 절반이다

기존 문들의 소켓은 `.runtime_governance_state/bridge/`에 살고, 그 디렉토리는 문이
`MVP_BRIDGE_CLIENT_GID`(어시스턴트의 gid)로 chgrp하며 **Hermes 컨테이너에
`/opt/bridge`로 마운트된다.** 워커 소켓이 거기 살면 Hermes가 문을 우회해 엔진을 직접
부를 수 있다 — 문이 거부한 kind·키를 되살리는 구멍이고, 분리가 무의미해진다.

따라서:

- 워커 소켓은 **새 하위 디렉토리 `.runtime_governance_state/internal/`** 에 둔다.
  Hermes 쪽 compose는 이 디렉토리를 마운트하지 않는다 (bridge/만 마운트한다는 것이 이미
  현재 상태다).
- 워커 문은 **uid allowlist를 필수로** 한다 (`resolve_client_uids` 재사용, 값은 런타임
  자신의 uid 10001). group-only로 두지 않는 이유: 어시스턴트 컨테이너는 시기에 따라
  런타임 gid를 `group_add`로 가진 적이 있어, 마운트 실수 하나가 곧 접근이 되는 것을
  gid가 막아주지 못한다. `SO_PEERCRED`의 uid는 커널이 connect 시점에 기록한 값이라
  어느 프레임도 고를 수 없다 — 이 저장소가 이미 확보해 둔 성질의 재사용이다.
- 테스트는 이름이 아니라 성질로 고정한다: *워커 소켓 경로는 bridge/ 아래가 아니다*,
  *워커 문은 uid allowlist 없이 listen을 거부한다*.

### 5.3 실패 방향

| 상황 | 응답 |
|---|---|
| 워커 소켓 부재·연결 거부·데드라인 초과 | `WORKER_UNAVAILABLE` — "런타임이 거부했다"(파이프라인 BLOCK)와 구분되는 typed 오류. Hermes는 "문 반대편이 죽었다"로 보고한다 |
| 워커가 죽고 요청이 멱등 id를 가졌던 경우 | 문의 기존 예외 경로 그대로 release — 오늘 in-process 예외와 동일 의미 |
| 파이프라인 BLOCK | 오늘과 동일: `ok: false` + 파이프라인의 `reason_code` 그대로 릴레이 |

---

## 6. PR 시퀀스와 배포 순서

### PR-A — 워커 신설 + 문 전환 (평면 R만 건드린다)

코드: `runtime/mvp_runtime/pipeline_worker.py` + `pipeline_worker_cli.py` (socket_door
재사용), `dispatch_bridge.py`의 실행 자리를 forward로 교체. compose: `pipeline-worker`
서비스 신설 (모델·검색·Naver env + Core ro 마운트 + 소켓 healthcheck), **dispatch-bridge의
env에서 provider·검색·Naver 전부 제거** (남는 것: `MVP_BRIDGE_CLIENT_GID/UID` 두 개 —
read/switch와 같은 모양).

테스트 이동 (`tests/test_deployment_env_passthrough.py`):

- `NAVER_RESEARCH_SURFACE`의 서비스 목록 `["scheduler", "dispatch-bridge"]` →
  `["pipeline-worker"]` (PR-B에서 scheduler 행이 빠지므로 PR-A에서는
  `["scheduler", "pipeline-worker"]` 경유).
- 부정 방향 신설: dispatch-bridge는 Naver·모델·검색 어느 것도 받지 않는다 (operator
  부정 테스트와 같은 형태, 같은 이유).
- 워커의 수신 매트릭스 (모델 체인·검색·Naver 전부, `${VAR:-}` 형태) — 이 파일이 존재하는
  이유 그대로: compose가 이름을 나르지 않으면 조용히 mock이다.
- 소켓 위치·uid 필수 property 테스트 (§5.2).
- `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named`는 **바뀌지 않는다** —
  `naver_research.py`의 호출 지점은 한 줄도 움직이지 않는다.

배포 (candidate 태그 규율 그대로, 순서가 요점):

```
1. rollback 태그 → candidate 빌드 (clean origin/main worktree)
2. docker compose up -d pipeline-worker        # 새 서비스만 먼저
3. 워커 검증: healthcheck + 런타임 uid 컨텍스트에서 소켓 왕복 1건
4. docker compose up -d dispatch-bridge        # 그 다음에야 문을 교체
5. e2e: 문 경유 dispatch 1건 → 원장에 assistant_bridge 귀속으로 완료 확인
```

살아 있는 dispatch 경로를 검증 안 된 워커로 한 번에 바꾸지 않는다 — halt 문 은퇴(#390)가
남긴 순서 교훈("스위치 문이 먼저 살아서 검증되고, halt는 그 다음에 은퇴")을 코드 폴백이
아니라 배포 순서로 지킨다. 이 창에서 재시작되는 것은 평면 R뿐이고 scheduler는 건드리지
않는다.

### PR-B — scheduler에서 Naver env 제거 (돈 평면의 재시작 창)

compose에서 scheduler의 Naver 다섯 줄 제거 + 위 테스트의 scheduler 행 제거. 코드 변경
없음 — scheduler에는 Naver 소비자가 없다 (§2). 분리하는 이유는 **thomas-scheduler
재생성은 돈 평면의 재시작**이라 자기 배포 창을 가져야 하기 때문이다 (열린 포지션·라이브
윈도우 고려는 그 창에서).

PR-B 이후 상태: **Naver env는 pipeline-worker에만 존재한다.** 그 다음에 Phase 0
(라이선스)을 진행하면, 값이 `.env`에 들어가는 첫날부터 반경이 워커 하나다.

### 블로그 레인 상신과의 정합

`NAVER_BLOG_CONTENT_LANE_V0.1.md` §3의 흐름(`dispatch (kind=research)` →
`dispatch (kind=content)`)은 그대로 성립한다 — 문 뒤에서 실행 위치만 워커로 바뀐다.
Phase 3의 `content_ideation` 스케줄은 §7의 상신에서 실행 위치(워커 위임)를 정한다.
해당 문서 §3 다이어그램에 한 줄 주석이 필요하다 (PR-A에 포함).

---

## 7. Phase ② 방향 — 돈을 옮기지 말고, 나머지를 옮긴다 (별도 상신 예고)

scheduler의 최종 형태에 대해 두 방향이 있다:

| | (a) 실행기 추출 — Binance 키를 새 executor 서비스로 | (b) 나머지 배출 — LLM 스케줄 실행을 워커로 위임하고 scheduler가 모델·검색 키를 내려놓는다 |
|---|---|---|
| CLOSE 경로 | **IPC 홉이 생긴다** — 불변식 2 위반 | 홉 0개, 오늘 그대로 |
| tick 계약 | 두 번째 tick 또는 store 파티션 필요 | 유지 — tick은 그대로, 실행만 위임 |
| 단일 관문 테스트 (`…exactly_one_module`) | 관문이 프로세스 경계를 넘어 이사 | 불변 |
| 움직이는 것 | 돈 경로 (실패 비용 최대) | fail-safe P3 작업 (실패 비용 최소) |

**권고는 (b)다.** 끝 그림은 같다 — scheduler가 곧 평면 M(Risk Loop + Live Executor)이
된다 — 그러나 이사하는 짐이 돈이 아니라 리서치다. 필요한 선행 결정(scheduler-발 위임
작업의 귀속 actor, pass budget과 위임 호출의 상호작용, `content_ideation`의 실행 위치)은
그 상신에서 다룬다. 여기서는 **방향만** 묻는다 (§8 D3).

Phase ③ 잔여 (각각 별건, 여기선 이름만): operator 서비스의 향방 (Telegram은 이미
Hermes로 갔다), state 마운트의 평면별 파티션 (env 분리가 1축, 마운트가 2축이다 — §9),
kind별 워커 세분화, 비동기 dispatch (180s 시임 타임아웃의 근본 해법).

---

## 8. Thomas 결정

| # | 결정 | 성격 | 권고 |
|---|---|---|---|
| **D1** | Naver env 보유 서비스를 `pipeline-worker` **하나**로 줄인다 (#650이 정한 scheduler·dispatch-bridge 배치를 뒤집는다 — #650 스스로 "서비스 추가는 config tweak이 아니라 결정"이라 못박은 그 결정이다) | compose + 테스트, 정책 변경 없음 | **채택, 라이선스 전에** (§2) |
| **D2** | dispatch 문은 네 kind 전부를 워커로 넘기고 자격증명 env 0개가 된다 (대안: research/content만 넘기고 문이 모델 키를 유지) | 구조 결정 | **전부** — 문이 read/switch와 같은 형태가 되고, 평면 R의 모델 키 컨테이너가 1개로 준다. 절반 이사는 두 번 이사다 |
| **D3** | Phase ②의 방향 = §7의 (b) "나머지를 옮긴다" | 방향 승인 (상세는 별도 상신) | **(b)** |

**정책 diff: 0.** 새 verb 없음, 새 권한 없음, kind 집합·P3 상한·귀속·승인 경로 전부
불변. 스위치 상신의 S1과 같은 "구현 승인" 분류다. `docs/ACTIVE_ARCHITECTURE.md`에
워커 등록 한 줄이 들어간다 (문 목록은 3개 그대로 — 워커는 문이 아니다, Hermes가 닿을
수 없으므로).

---

## 9. 이 제안이 고치지 않는 것 (정직하게)

- **state 볼륨은 여전히 전 서비스 rw 공유다.** 이 제안은 자격증명 반경을 자르지, 상태
  반경은 못 자른다. 워커가 뚫리면 원장·control 상태에 쓸 수 있는 것은 오늘의
  dispatch-bridge와 같다. 마운트 파티션은 Phase ③의 자기 상신감이다.
- **워커도 신뢰하지 않는 텍스트를 처리한다.** 요청 본문은 여전히 Hermes를 거쳐 온
  텍스트다. 보증은 오늘과 동일하게 구조(P3 상한, 닫힌 kind, `write_path` 부재)이고, 이
  제안은 그 보증을 강화하는 게 아니라 **보증이 깨졌을 때 잃는 것**을 줄인다.
- **OPENROUTER 키는 여전히 Hermes 자신과 공유된다.** 키 분리는 이 제안 범위 밖이다.
- **컨테이너 +1.** 배포·모니터링 표면이 늘고, 동시 세션 배포 규율의 부담도 그만큼 는다.
  요청당 로컬 홉 1개가 추가되지만 분 단위 모델 실행 앞에서 ms는 소음이다.

---

## 부록 — 가드레일 자기 점검

| 가드레일 | 어떻게 지키는가 |
|---|---|
| 재사용 우선 | 신설 개념 0개 — `socket_door`·`run_task`·멱등·env passthrough 테스트·candidate 배포 규율 전부 기존 것. 새로 짓는 것은 서비스 1개와 그 CLI뿐 |
| 하나의 개념 = 하나의 authority | 검증의 authority는 문 하나. 워커의 재검증은 방어이지 두 번째 판정자가 아니다 (거부 사유는 문의 것이 우선한다) |
| fail-closed | 워커 불달 → typed 거부. 폴백 없음 (불변식 3). uid 미설정 → listen 거부 |
| 문은 권위를 갖지 않는다 | 바뀌지 않는다 — 오히려 문에서 실행 능력을 제거해 문을 더 문답게 만든다 |
| 돈 경로 격리 | 워커에 `BINANCE_*`·`MVP_LIVE_*` 없음. 돈 경로에 홉 추가 없음 (불변식 2) |
| Claude는 라이브 머니 패스를 만지지 않는다 | 이 문서는 제안이다. PR-B의 scheduler 재시작 창을 여는 것도 Thomas의 결정이다 |

---

*근거: `docker-compose.yml` (여섯 서비스의 environment 블록과 그 주석),
`runtime/mvp_runtime/dispatch_bridge.py` (`apply_dispatch`, `MAX_CONCURRENT_REQUESTS`,
provider 주입), `runtime/mvp_runtime/socket_door.py` (`resolve_client_uids`, gid 명시,
SO_PEERCRED), `runtime/mvp_runtime/naver_research.py` (`select_env_gated`, 호출자 부재),
`tests/test_deployment_env_passthrough.py` (표면 정의와 그 반전 이력), PR #650 본문,
`docs/proposals/NAVER_BLOG_CONTENT_LANE_V0.1.md` §3·§5·§6,
`docs/proposals/HERMES_AGENT_DISPATCH_V0.1.md`·`HERMES_AGENT_SWITCH_V0.1.md` (문 패턴과
상신 형식), `CLAUDE.md` (env-only 게이트 3종과 CLOSE 논리, candidate 태그 규율).*
