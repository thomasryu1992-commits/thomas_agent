# Door API v2 — design record v0.1

**Status:** PARTIALLY IMPLEMENTED — 제안 1(typed/versioned frame)은 PR7로 landed(`socket_door.validate_envelope`/`envelope`/`refusal_payload`, `PROTO_UNSUPPORTED`, 문 4개의 `proto`·`client_id` 수용과 `data`, 멱등 지문의 봉투 키 제외). 제안 2(registry origin AGENT)는 PR8로 landed(`WORKER_ORIGINS`·워커 기록·`created_by`·`find`의 task_/trace_ 폴백·콘솔 표식·워커 CLI reconcile). 제안 3(멱등 v2)은 PR9로 landed(replay `data`에 `status`·`result`·`result_ref`, `REQUEST_IN_FLIGHT`가 `data.status=RUNNING`을 실음, `MAX_REPLAY_RESULT_BYTES` 초과 시 참조로 강등). 제안 4(조회 verb 4종)는 PR10으로 landed(`store_reads` 모듈, `_STORES` family, `READ_VERB_AUTHORITY` 양방향 테스트, 스케줄·승인 저장소 주입). 제안 5(미러링)는 아직 unbuilt. Thomas fixed the *scope* on 2026-09-03 (`docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.1.md` §4); this record turns that scope into concrete changes, prices each against the code and the tests that pin it, and names the six branch points that are still Thomas's (D-1…D-6).
**Owner:** Thomas.
**Authority:** None. The door modules, `task_registry.py`, `bridge_idempotency.py` and the committed policy are the authority for what the runtime does; this record explains what would change and why it is shaped that way. It changes no policy or schema file itself.
**Raised:** 2026-09-04, after PR1–PR5 of the harness sequence and the #831 fix.
**Baseline:** `origin/main` `9d8e894`; every `path:line` below is that commit. Hermes shims are `/root/hermes-trial/data/mcp/*.py` (`D/`).

---

## 한 문장으로

Hermes가 오케스트레이터가 되려면 문이 **기계가 읽는 응답, 회수 가능한 런, 멱등한 재전송, 스케줄·승인의 조회**를 줘야 하는데, 지금 문은 사람이 읽는 콘솔이고 어시스턴트 런은 원장에 떨어질 뿐 어디에서도 다시 찾을 수 없다.

## 먼저 읽을 것 — 이것은 문제가 *아니다*

- **문의 경계는 바뀌지 않는다.** uid 게이트(`socket_door.py:346-360`), 승인 경로(관제봇 1:1, `approval.py:350-361`), 자격증명 평면, kind 4종·P3(`dispatch_bridge.py:93`), "read 문에 변경 verb 없음"(`tests/test_mvp_runtime_read_bridge.py:49-63`)은 전부 그대로다. v2는 프레임·기록·조회를 더할 뿐 권한을 하나도 더하지 않는다. `gate_grants_authority: false`는 이 기록에도 적용된다.
- **스케줄 변경 verb는 만들지 않는다** (결정 Q4·Q13). 조회만.
- **취소·비동기 핸들·도메인 부분정지는 이 기록 밖이다** (D-1 취소는 2단계; 자리만 §"2단계"에).
- **정책 파일은 이 기록이 건드리지 않는다.** 정책 파일은 바이트 하나가 바뀌어도 리플레이 번들 2개(sha 4곳+bundle_sha256)를 재생성해야 하므로(`scripts/validate_i0_5_read_only_runtime.py:145-152`; `runtime/read_only_kernel/preflight.py:143-152`) 조문은 §"정책 1.5.0" 초안으로 모아 Thomas가 한 번에 적용한다(결정 Q2).
- **`client_id`는 귀속 메타이지 신원이 아니다.** 신원은 SO_PEERCRED uid뿐이다(`socket_door.py:481-501`). 프레임 값으로 권한을 나누는 설계는 없다.

## 실제 간극 — 코드가 말하는 것

| 오케스트레이터가 필요로 하는 것 | 지금 | 근거 |
|---|---|---|
| 구조화 응답 | 응답 봉투에 공통 메타를 넣는 곳이 없고 각 문이 dict를 통째로 만든다; read는 `{ok, command, reply(str), action}` 4키 고정, `data` 없음 | `socket_door.py:234-238`; `read_bridge.py:148-153` |
| 클라이언트 신원·버전 | 프레임에 둘 다 없음; actor는 상수 `assistant_bridge` 하나(`ASSISTANT_ACTOR`), 어떤 필드로도 덮어쓸 수 없음 | `socket_door.py:68`; `pipeline_worker.py:134-139` |
| 회수 가능한 런 | 워커는 `task_registry`를 import조차 않고(`apply_work`에 registry 인자 없음), read 문 `result`는 `registry_entry_id` prefix만 매치 — `task_…`는 `ENTRY_NOT_FOUND` | `pipeline_worker.py:178-190,521-531`; `registry_console.py:362-376`; `task_registry.py:387-404` |
| 멱등 재전송 | 서버는 claim/complete/replay를 구현했지만 outcome은 `{kind, task_id, ok}`뿐; shim 4벌은 `request_id`를 보내지 않는다 | `dispatch_bridge.py:275-283`; `bridge_idempotency.py:207-225`; `D/*_mcp.py`(grep 0) |
| 스케줄·이벤트·하트비트·승인 상태 조회 | read 문 verb 9종·family 4종 어디에도 없음; 하트비트는 CLI 프로브뿐, 승인 조회 verb는 채널·문 어디에도 없음 | `read_bridge.py:53-77`; `heartbeat_cli.py:33-45`; `approval_cli.py:87-125` |
| 승인 요청 알림 | operator가 switch ask만 골라 등록된 chat 하나에 관제봇 토큰으로 푸시; 배치 3건, 포인터 파일 하나 | `operator.py:902-971` |

## 부하 검토 (결정 Q3의 선행 조건) — 2026-09-04 실측

"어시스턴트 런을 레지스트리에 기록하면 부하가 걸리는가"에 대한 답은 **아니다**, 수치로:

| 항목 | 실측 | 함의 |
|---|---|---|
| `task_registry.jsonl` | 19,511 B, 35행(전이 로그), **항목 7건, 전부 DELIVERED**, origin 전부 TELEGRAM | 미종결 RUNNING 없음 — reconcile은 크래시 대비용 |
| 어시스턴트 dispatch 런(원장 전 기간) | **30건**(`records.jsonl` 21.7 MB + 아카이브 77파일) | 기록 시 항목당 2행(RUNNING→terminal, `task_registry.py:137-154`) ≈ 1 KB. 하루 수십 건이 되어도 연 1 MB 미만 |
| read 문 응답 시간(hermes uid 10000에서) | `runtime_status` 1 ms, `tasks` 2 ms, `history 20` 1 ms, `crypto_status` 2,951 ms | 레지스트리 렌더는 밀리초; 비용은 크립토 보드 렌더에 있다(v2 무관) |
| 레지스트리 락 | `fcntl.flock(LOCK_EX)` 블로킹, 모든 읽기가 락 안에서 파일 전량 파싱(`task_registry.py:302-358`); 회전 없음 | 행 단위 append; 파일이 작아 전량 파싱은 밀리초. 회전은 항목 수천 건에서 재검토 |

결론: origin `AGENT` 기록은 **켠다**. 남는 설계 질문은 부하가 아니라 "누가 어디서 쓰고, 누가 회수하는가"(§제안 2).

## 제안 1 — typed / versioned frame

**요청** (모든 문 공통, 전부 optional — 없으면 v1 그대로):

| 키 | 형 | 의미 |
|---|---|---|
| `proto` | int | 프로토콜 버전. 없으면 1. `{1, 2}` 밖이면 `PROTO_UNSUPPORTED`(새 코드, `ControlBlocked`) |
| `client_id` | str ≤ 64, `[A-Za-z0-9._:-]` | 귀속 메타(예: `hermes:dm`, `hermes:cron:silent-watch`, `hermes:delegate:3`). 권한 판단에 쓰지 않는다 |
| `request_id` | str ≤ 128 | 기존(`bridge_idempotency.py:82-96`) |

**응답** (성공·문이 만든 거부 봉투 공통, 전부 additive):

| 키 | 의미 |
|---|---|
| `proto` | 요청에 있었을 때만 echo |
| `request_id`, `replayed` | 기존 |
| `reply` (str) | 그대로 — read 테스트가 str을 고정(`tests/test_mvp_runtime_read_bridge.py:110-123`), shim의 `_stamp`가 str을 전제 |
| `data` (object \| null) | 구조화 본문. 문·verb마다 스키마는 §각 제안. **항상 additive**로 붙인다(v1 클라이언트는 무시) |

전송층이 `apply` 밖에서 만드는 거부 봉투(`BRIDGE_BUSY`·`PEER_*`·`MALFORMED_REQUEST`·`BRIDGE_ERROR`, `socket_door.py:188-196,222,228,404-411`)에는 `proto`·`data`·`request_id`가 실리지 않는다 — v2 클라이언트는 이 세 키를 optional로 다룬다.

**어디를 바꾸는가.**
- 키 수용: read는 미지 키를 무시하므로 변경 없음(`read_bridge.py:105-121`); switch `_ALLOWED_KEYS`(`switch_bridge.py:118-121`), dispatch `_ALLOWED_KEYS`(`dispatch_bridge.py:103-105`), knowledge verb별 3집합(`knowledge_bridge.py:71-78`)에 `proto`·`client_id` 추가. 키 집합을 `==`로 고정한 테스트는 없다(판독 확인); 부재를 단언하는 테스트(`actor_profile`·`job`·`inventory`·`write_path`…)는 무관.
- 검증·echo 헬퍼는 `socket_door`에 한 곳: `negotiate_proto(request) -> int`와 `envelope(payload, *, proto, request_id=None, data=None) -> dict`. 네 문이 같은 헬퍼를 써야 봉투가 갈라지지 않는다.
- **멱등 지문에서 `proto`·`client_id`를 제외**한다. 지금 지문은 `request_id`를 뺀 프레임 전체(`bridge_idempotency.py:99-110`)라, 재전송 사이에 `client_id`가 달라지면 같은 `request_id`가 `REQUEST_ID_REUSED`가 된다. `test_the_fingerprint_ignores_the_id_and_nothing_else`(`tests/test_mvp_runtime_bridge_idempotency.py:93-100`)를 "id와 봉투 키를 제외한다"로 갱신.
- 워커 전달 프레임(`dispatch_bridge.py:318-320`)에 `client_id`를 더하고 워커 `_ALLOWED_KEYS`(`pipeline_worker.py:63-66`)에도 추가 — D-2가 `created_by`를 택할 때만. `proto`는 워커로 보내지 않는다.
- `PROTO_UNSUPPORTED`는 `MALFORMED_REQUEST`를 정확 단언하는 테스트들(read 179-185, knowledge 92-98, dispatch 105-111, switch 191-194)이 `proto` 없는 프레임을 여전히 통과시키도록 optional로만 검사한다.
- `DIAGNOSTIC_CODE_INDEX` 재생성(새 reason_code 1개).

**하지 않는 것.** 프레임 8 KiB 상한(`socket_door.py:111`)과 초과 시 무응답 종료(`:240-254`)는 그대로 둔다 — 넓히면 `test_the_default_frame_ceiling_is_unchanged_for_the_console_doors`(`tests/test_mvp_runtime_knowledge_bridge.py:266-284`)를 깨고, 한글 팽창은 shim의 `ensure_ascii=False`(§Hermes 쪽)로 절반이 된다.

## 제안 2 — 어시스턴트 런의 레지스트리 기록 (origin `AGENT`)

**원칙.** 원장이 권위, 레지스트리는 포인터(`task_registry.py:8-12`). 워커는 어시스턴트 프로필 런만 기록하고, **`RUNNING`으로 직접 연다** — `QUEUED`를 거치면 오퍼레이터 drain(`claim_next_queued`, origin 무관 FIFO, `task_registry.py:364-385`)이 집어가 텔레그램으로 결과를 보내려 하고 `QUEUE_DEPTH_LIMIT` 20을 오퍼레이터와 나눠 쓴다.

**변경.**
1. `task_registry.ORIGINS`에 `"AGENT"`(`:95`) **와** `schemas/task_registry_entry.v0.2.schema.json:40-45` origin enum을 **동시에**. 한쪽만 바꾸면 `_validate`가 `REGISTRY_RECORD_INVALID`를 던지고 `record_submission`이 삼켜 `None`을 돌려주므로(`:548-557`) **아무 오류 없이 기록이 빠진다** — 이 조용한 실패를 테스트로 박는다(스키마·상수 동치 테스트). 버전은 D-1.
2. 새 소유 집합 `WORKER_ORIGINS = frozenset({"AGENT"})`(`:102-103` 옆). `pipeline_worker_cli` 시작 시 `reconcile_stale_running(store, now=…, origins=WORKER_ORIGINS)` — 지금 reconcile 호출자는 operator·scheduler 두 곳뿐이라(`operator_cli.py:161-167`; `scheduler_cli.py:373-378`) 워커가 죽으면 `AGENT RUNNING`을 닫을 프로세스가 없다. `test_the_two_ownership_sets_are_disjoint_and_known_origins`(`tests/test_mvp_runtime_task_registry.py:246-250`)를 세 집합으로 확장.
3. `pipeline_worker.apply_work`·`open_door`에 `registry: TaskRegistryStore | None` 추가(`:178-190,521-531`), `pipeline_worker_cli.py:99-108`에서 `TaskRegistryStore.default()` 배선. 기록 지점: kill 스위치 통과 뒤(`:281-286`) `run_task` 직전(`:291`)에 `record_submission(registry, request_text=text.strip(), origin="AGENT", requester_id=profile["requester_id"], now=timeutil.utc_now_iso(), request_kind=kind)` — `now`는 문이 보내지 않으므로 워커가 만든다(None이면 `submitted_at` 패턴 위반으로 또 조용히 미기록). 종료는 `_identity(result)`(`:312`) 뒤: COMPLETED→`DELIVERED(task_id, trace_id, result_ref=f"ledger:{trace_id}")`, 그 외→`BLOCKED(reason_code)`; `run_task`를 `try/except BaseException`으로 감싸 `close_entry(FAILED, reason_code="WORKER_EXCEPTION")` 후 re-raise — 아니면 RUNNING 잔류가 정상 경로가 된다(`:281-334`).
4. **스케줄러 프로필 프레임은 기록하지 않는다** — `scheduler.py:1816-1838`이 이미 `SCHEDULER` 항목을 열어 두므로 워커가 또 열면 한 실행이 두 항목이 된다. 테스트로 고정.
5. 워커 응답에 `registry_entry_id` 추가(`:313-334`); 문이 그대로 중계한다.
6. `registry_console._require_entry`(`:362-376`)·`TaskRegistryStore.find`(`:387-404`)에 `task_`·`trace_` 접두 정확 매치 폴백 — D-3.
7. `/tasks`·`/history` 렌더에 AGENT 항목 표식(`registry_console.py:116-143`) — 기존 정확 문자열("• " 개수, "7분 경과")은 유지되도록 표식은 항목 줄 안 접두로만.
8. 워커 동시성 2(`pipeline_worker.py:155`)라 `AGENT RUNNING`이 동시에 둘일 수 있다 — 문서·렌더가 "한 번에 하나"를 가정하지 않게.

**깨지는 테스트(의도).** `test_the_two_ownership_sets…`(확장), `test_operator_kind_markers.py:54-57`(D-1이 v0.3이면), pipeline_worker 응답 형태를 단언하는 테스트(`registry_entry_id` 추가는 additive라 대부분 통과), 새 테스트: 조용한 미기록 방지·프로필 분기·예외 시 FAILED·reconcile 배선.

## 제안 3 — 멱등 v2: `request_id` 재전송은 `{task_id, status, result}`를 돌려준다

**지금.** dispatch는 `request_id`를 받고(`_ALLOWED_KEYS`) claim→execute→complete를 한다(`dispatch_bridge.py:152-260`); complete outcome은 `{kind, task_id, ok}`(`:275-283`); 재전송은 `replay_reply` 6키(`bridge_idempotency.py:207-225`)로 `outcome`만 돌려준다. shim은 `request_id`를 보내지 않으므로 이 경로는 **실사용에서 한 번도 돌지 않았다**.

**변경.**
1. outcome에 `registry_entry_id`(워커 응답에서) 추가 → `{kind, task_id, ok, registry_entry_id}`. `test_the_record_carries_no_payload`(`:183-190`)가 원장 행에 본문 금지를 고정하므로 **결과 본문은 outcome에 넣지 않는다**.
2. 재전송(replay) 시 dispatch 문이 outcome의 `registry_entry_id`로 레지스트리를 찾아 `status`를 붙이고, `DELIVERED`면 `registry_console._rerender_from_ledger`(`:182-221`)로 `result`(텍스트)를 재렌더한다. 응답: `{ok, replayed: true, request_id, outcome, data: {task_id, registry_entry_id, status, result?}}`. 이를 위해 dispatch 문이 `registry`를 연다(`apply_dispatch` `:138-145`에 인자 추가, CLI 배선). 재렌더는 원장 스트림 스캔(`ledger.iter_records()`, 락 유지)이라 비용이 있다 — D-4.
3. 진행 중 재전송: `REQUEST_IN_FLIGHT`는 유지하되(테스트가 코드 고정) `data: {status: "RUNNING"}`을 붙인다. claim 시점엔 `registry_entry_id`가 없다(워커가 완료 시에만 돌려줌) — 클라이언트는 `tasks`로 본다.
4. 문→워커 프레임에는 `request_id`를 계속 보내지 않는다(워커 `_ALLOWED_KEYS`, `pipeline_worker.py:59-66`).
5. `IDEMPOTENCY_UNAVAILABLE`(ledger 없는 문에 `request_id`, `dispatch_bridge.py:200-209`)은 그대로 — 배포 체크리스트에 "dispatch-bridge가 ledger를 가지고 떴는가"를 추가.
6. switch `enable`(ask/spend)도 이미 claim/complete를 한다(`switch_bridge.py:648-716`); v2에서는 shim이 `request_id`를 붙이기만 하면 된다. `status`/`disable`은 echo만(dedup 없음, `:249-262`) — 그대로.

## 제안 4 — read 문 조회 verb 4종 (변경 verb 없음)

새 family 상수 `_STORES`(`read_bridge.py:53`)와 dispatch 분기(`:124-146`, `else`가 memory를 흡수하므로 명시 `elif`). `open_door`·`apply_read`에 `schedules`·`approval_store`·`repo_root` 주입(`:85-94,163-184`; CLI `read_bridge_cli.py:49-55`) — `.default()` 암묵 사용은 테스트 격리(tmp_path)를 깬다. 스토어 미주입 시 typed 거부(`SCHEDULES_UNAVAILABLE` 등) — `test_every_table_entry_dispatches_without_an_unhandled_error`(`tests/test_mvp_runtime_read_bridge.py:110-123`)가 요구.

| verb | 인자 | `reply` | `data` | 재료 | 비용·주의 |
|---|---|---|---|---|---|
| `schedules` | 없음 | `render_schedule_summary`(`scheduler.py:624-655`) 재사용 | 활성 행 목록: `schedule_id, kind, lane(파생: kind ∈ RISK_KINDS), interval_seconds, enabled, next_run_at, overdue_seconds(overdue_schedules :658-677), last_status(60자 절단)` | `ScheduleStore.list()` — 무락, 원자 교체 파일(`:506-535`) | 한 행이라도 `SCHEDULE_RECORD_INVALID`면 전체 실패(fail-closed 유지). `lane`은 스토어 필드가 아님을 `data`에 명시 |
| `scheduler_events [n]` | `parse_count_arg`(기본 20·최대 100, `control.py:178-207`) | 최근 n건 한 줄씩 | 행 그대로(`record_type, action, schedule_id, kind, status, created_at, …`) | `LedgerStore.read_scheduler_events()` — append 락 아래 전량 materialize(`store.py:436-444`), 활성 파일 ≤ ~2000행(회전 `retention.py:62,74-82`) | 아카이브는 따라가지 않는다(창 = 최근 ~2000행). 폴링은 tick 루프의 append를 그 시간만큼 막는다 — 60초 미만 폴링 금지를 SOUL에 |
| `heartbeat` | 없음 | `"{service}: {status} — {detail}"` ×3 | `check_heartbeat` dict ×3(`heartbeat.py:108-137`: status FRESH/STALE/MISSING/UNREADABLE, age_seconds, stale_after_seconds, pid) | 무락 파일 읽기 | 서비스 3종 `operator`·`scheduler-risk`·`scheduler-maintenance`(단일 프로세스 배포에선 MISSING이 정상) |
| `approval_status <approval_id>` | approval_id | 한 줄 요약 | `{approval_id, status_recorded, status_effective, expires_at, issued_at, target_prefix, scope, consumed_at, decided_at}` | `ApprovalStore.get()` + `approval.is_expired(now)`(`approval_store.py:69-94`; `approval.py:222-223`) | `pending()`은 만료를 걸러내지 않고 EXPIRED 전이는 Thomas가 답할 때만 기록된다(`approval.py:690-696`) → `status_effective`를 계산해 준다. **레코드 본문(스냅샷·지문·permission_decision)은 내보내지 않는다** — read 문의 "approvals/ 비노출" 설계(`read_bridge.py:6-8`). 읽기 속 쓰기(EXPIRED append) 금지 |

`_TAKES_ARGUMENT`(`:77`)에 `scheduler_events`·`approval_status` 추가. control family가 아니므로 `test_the_table_names_no_control_verb_that_changes_state`(`:57-63`)와 `test_every_bridge_domain_read_resolves…`(`:138-149`, "domain" 문자열 재사용 금지)는 그대로 통과. 응답 크기는 렌더러가 규율한다 — 서버·shim 모두 응답 상한이 없다(`socket_door.py:236`; `D/read_bridge_mcp.py:73-85`).

**권한 근거 인벤토리.** 채널에는 `CHANNEL_VERB_AUTHORITY`(`operator.py:127-166`)와 양방향 동등 테스트(`tests/test_mvp_runtime_control.py:510-561`)가 있지만 read 문에는 없다. `read_bridge.READ_VERB_AUTHORITY`를 같은 모양으로 신설하고 `_READS`와 양방향 동등을 테스트로 고정한다 — 그래야 §정책 1.5.0의 `assistant_read` 조문이 장식이 아니다(정책의 `role_allowlist`는 어떤 테스트도 읽지 않는다: 판독 grep 0건).

## 제안 5 — 승인 요청 알림의 Hermes 창 미러링 (결정은 관제봇)

**메커니즘은 이미 검증돼 있다.** 스케줄러 레인이 `HERMES_BOT_TOKEN`으로 등록된 `chat_id`에 보내면 Hermes 창에 도착한다(2026-08-01 실측, `docker-compose.yml:207-226`). 미러링은 operator에 같은 것을 한 번 더 붙이는 일이다.

**변경.**
1. 발신 전용 채널: `TelegramChannel(token_env="HERMES_BOT_TOKEN", authorization=…, state_path=None)`(`operator.py:987-1008`)을 `SendOnlyChannel` 래퍼로 감싼다 — `poll`/`peek`가 `OperatorBlocked("MIRROR_IS_SEND_ONLY")`를 던지도록. 한 봇 토큰에 poller가 둘이면 서로 업데이트를 훔친다(`docker-compose.yml:225-230`); hermes 컨테이너가 그 봇의 유일한 poller여야 한다. 셀렉터 `select_mirror_channel()`은 `select_operator_channel`(`:801-823`)과 같은 env 게이트(`MVP_OPERATOR_CHANNEL=telegram`)를 쓰고, 실패 시 서비스가 죽지 않게 stderr 경고 후 `None`(scheduler의 `build_alerter` 패턴, `scheduler_cli.py:97-113`).
2. `announce_pending_approvals(channel, approval_store, *, now, repo_root=None, mirror=None)`: 주 발신(`notify_operator`) 직후 `mirror`에 **별도 본문**을 best-effort로(`try/except MvpRuntimeError`) — 실패는 stderr 한 줄, 포인터는 주 발신 기준 그대로(D-5). 본문은 `approval.request_message` 원문의 마지막 두 줄(`가능한 선택: /approve {id} | /reject {id}`, `approval.py:494`)을 **빼고** "결정은 관제봇 창에서 `/approve {id}` — 이 창의 /approve는 아무에게도 닿지 않습니다"를 붙인다. 그대로 미러링하면 Hermes 창에서 `/approve`를 치도록 유도한다(shim이 이미 그 혼동을 경고한다, `D/switch_bridge_mcp.py:177-183`).
3. `operator_cli.main`에 `mirror_channel=None` 주입 인자, `gate_banners`에 함께 전달(`operator_cli.py:85-138`).
4. compose: operator에 `HERMES_BOT_TOKEN: ${HERMES_BOT_TOKEN:-}` — **컨테이너 안 이름을 `TELEGRAM_BOT_TOKEN`과 다르게**(operator는 관제봇을 poll하므로 레인식 fallback 별칭 금지). `SECRET_OWNERSHIP["HERMES_BOT_TOKEN"]`에 `operator` 추가(`tests/test_deployment_env_passthrough.py:579`)와 `docs/DEPLOYMENT.md` 표 행(`:416`)을 같은 PR에서. **비밀 경계가 한 칸 넓어진다**(관제봇 토큰을 가진 서비스가 Hermes 봇 토큰도 갖는다) — 결정 Q1-b의 직접 결과이지만 명시한다.
5. 테스트: 기존 announce 정확집합 테스트(`tests/test_mvp_runtime_operator.py:1001-1116`)는 `mirror=None`으로 그대로 통과; 미러 테스트는 **별개 `MockOperatorChannel`**로(같은 객체면 `len(ch.sent)` 단언이 깨진다). 감사 이벤트 타입은 새로 만들지 않는다(`audit.py:701-706`).

**대상은 switch ask뿐이다**(`operator.py:927-938`의 필터 그대로). 메모리 승격·전략 풀 승인은 지금도 푸시되지 않고, 이 기록은 그것을 바꾸지 않는다.

## Thomas가 답할 분기점

| id | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **D-1** | origin enum 확장 방식 | (a) `task_registry_entry.v0.2` 스키마 in-place(enum에 값 추가) (b) v0.3 신설 | **(a)**. enum 추가는 additive이고 읽기 경로는 스키마를 검증하지 않는다(`task_registry.py:156-193`). (b)는 `SCHEMA_VERSION`·`$id`·파일명·`tests/test_operator_kind_markers.py:54-57`을 연쇄로 바꾼다 |
| **D-2** | `client_id`를 task 레코드 어디에 남기나 | (a) `audit.created_by = "assistant_bridge:{client_id}"` (b) `source_ref` 접두 (c) 안 남김(원장 밖 봉투 echo만) | **(a)**. `created_by`는 `task.v0.3`의 자유 문자열이고(`schemas/task.v0.3.schema.json:659-671`) 워커가 지금 안 쓴다(`intake.py:310` 기본값). `requester_id`는 **금지** — `dispatch_spend.py:41`의 `== "assistant_bridge"` 정확 동등이 깨져 $50/일 감시에서 빠지고 `intake.py:199-208`의 id seed가 바뀐다. (b)는 `test_the_source_ref_reads_identically_across_the_split`(`tests/test_mvp_runtime_pipeline_worker.py:200-203`)와 180자 절단에 걸린다 |
| **D-3** | `result`가 `task_`·`trace_` id도 받나 | (a) 정확 매치 폴백 추가 (b) `treg_`만 유지, shim 파라미터명만 `registry_entry_id`로 | **(a)**. shim의 `task_result(task_id)` 명칭이 지금도 오해를 만든다(`D/read_bridge_mcp.py:178-181`); 접두가 달라 `AMBIGUOUS_ENTRY_ID` 규칙(`task_registry.py:399-403`)과 충돌하지 않는다 |
| **D-4** | 재전송 응답의 `result` | (a) 본문 인라인(재렌더) (b) `result_ref`만, 본문은 read 문 `result` | **(a)** — Thomas의 09-03 결정 문구 그대로. 비용은 원장 스캔 1회(~23 MB, 락 유지)이고 어시스턴트 런은 누적 30건이라 감당된다. 본문이 1 MiB(`call_door` 클라이언트 상한)를 넘으면 (b)로 강등 |
| **D-5** | 미러 실패 처리 | (a) best-effort, stderr 한 줄, 포인터는 주 발신 기준 (b) 미러 전용 포인터로 재시도 | **(a)**. 관제봇 푸시가 권위이고 미러는 편의다; (b)는 포인터 파일이 둘이 되어 announce 루프의 부분 실패 창(`operator.py:951-971`)을 넓힌다 |
| **D-6** | 정책 1.5.0 시점 | (a) PR7–PR11이 모두 머지·배포된 뒤 한 번 (b) PR마다 | **(a)**. 정책 파일 변경마다 번들 재생성이 따르고, 범프는 PENDING 승인 0건 시점에 원자적으로(8c1cb02 관례) |

## 만들게 될 것 — PR 단위 (앞 PR 머지·배포 뒤 시작, 서버가 shim보다 먼저)

| PR | 범위 | 파일 | 깨지는·새 테스트 |
|---|---|---|---|
| **PR7** 프레임 v2 | `negotiate_proto`·`envelope`·`PROTO_UNSUPPORTED`; 문 4개 키 수용·`data` additive; 지문 제외 | `socket_door.py`, `read_bridge.py`, `switch_bridge.py`, `dispatch_bridge.py`, `knowledge_bridge.py`, `bridge_idempotency.py`, `errors.py`(코드), `DIAGNOSTIC_CODE_INDEX` | 지문 테스트 갱신; 새: proto 협상, 봉투 echo, 거부 봉투에 `data` 없음 |
| **PR8** 레지스트리 AGENT | `ORIGINS`+스키마(D-1), `WORKER_ORIGINS`, 워커 기록·예외·reconcile, 응답 `registry_entry_id`, `result` 폴백(D-3), 렌더 표식, `created_by`(D-2) | `task_registry.py`, `schemas/task_registry_entry.v0.2.schema.json`, `pipeline_worker.py`, `pipeline_worker_cli.py`, `registry_console.py`, `docs/runtime-contracts/TASK_REGISTRY_V0.1.md`(origin 표) | 소유 집합 테스트 확장; 새: 조용한 미기록 방지, 프로필 분기, FAILED on exception, reconcile 배선, `/tasks` 표식 |
| **PR9** 멱등 v2 | outcome `registry_entry_id`, replay 보강(status·result), in-flight `data` | `dispatch_bridge.py`, `dispatch_bridge_cli.py`, `bridge_idempotency.py` | `test_a_repeated_dispatch_runs_once` 유지; 새: replay 보강, in-flight status |
| **PR10** 조회 verb | `_STORES` family·4 verb·주입·렌더러·`READ_VERB_AUTHORITY`+양방향 테스트 | `read_bridge.py`, `read_bridge_cli.py`, (렌더러 모듈 1개) | read 테이블 테스트 유지; 새: 4 verb 각각·미주입 typed 거부·인벤토리 동등 |
| **PR11** 미러링 | `SendOnlyChannel`·`select_mirror_channel`·`announce(mirror=)`·compose·매트릭스·문서 | `operator.py`, `operator_cli.py`, `docker-compose.yml`, `tests/test_deployment_env_passthrough.py`, `docs/DEPLOYMENT.md` | 매트릭스 두 테스트 갱신; 새: 미러 본문·best-effort·send-only |
| **shim v2** (호스트) | §Hermes 쪽 | `D/mcp/*` | shim 단위테스트 신설(소켓 I/O 분리) |
| **정책 1.5.0** (Thomas 적용) | §정책 1.5.0 | `governance/GOVERNANCE_POLICY.yaml` + 리터럴 3곳 + examples·fixtures + 번들 2개 | validator·번들 게이트 |

배포는 PR마다 candidate-태그 절차(`CLAUDE.md`), 문 4개는 이미지 공유라 `up -d`로 함께 재생성. **shim v2는 PR7이 배포된 뒤에만** — 그 전에 `proto`·`client_id`를 보내면 switch/dispatch/knowledge가 `ARGUMENT_NOT_ACCEPTED`로 거부하고, `stop_trading`까지 막힌다(read만 조용히 통과해 "일부만 된다"로 보인다).

## 2단계 (이 기록의 범위 밖, 자리만)

- **비동기 dispatch 핸들**: `submit`→즉시 `{task_id, registry_entry_id, status: RUNNING}`→`tasks`/`result`로 폴링. 워커 안 드레인 루프가 필요하고, dispatch 슬롯(2)이 워커 완료(≤600 s, `dispatch_bridge.py:130`)까지 점유되는 구조(`:287-293`)를 바꾼다. shim의 280 s 상수(`D/dispatch_bridge_mcp.py:43`)와 `STARTED_BUT_SLOW` 분기가 통째로 대체된다.
- **취소**: `RUNNING→CANCELLED`는 불법 전이(`task_registry.py:74-93`)이고 abort 경로가 없다. 변경 verb이므로 read 문이 아니라 dispatch 문의 새 command여야 한다. 결정 기록 D-1(b) 조건이 채워질 때.

## 정책 1.5.0에 넘길 것 (이 기록은 정책을 바꾸지 않는다)

`control_channel` 섹션 끝(`GOVERNANCE_POLICY.yaml:322` 다음, `:324` 앞)에 additive 블록 — validator는 키 지정 검사라 무해하지만 sha가 바뀌므로 번들 2개 재생성이 따른다.

```yaml
  assistant_read:                       # the read door — named for the same reason 1.4.0 named the switch
    actor: assistant_bridge
    authority: [policy_dispositions.ALLOW.INTERNAL_READ, kill_switch.kill_allows.read_only_status]
    verbs:                              # closed; pinned by read_bridge.READ_VERB_AUTHORITY <-> _READS
      - runtime_status
      - crypto_status
      - crypto_readiness
      - crypto_paper
      - crypto_funds
      - tasks
      - history
      - result
      - memory
      - schedules                       # v2: rows only; enable/disable/remove stay in scheduler_cli
      - scheduler_events
      - heartbeat
      - approval_status                 # v2: summary only; approvals/ records are never exposed
    mutation_allowed: false
    approval_status_exposes: [approval_id, status, expires_at, target_prefix, scope]   # never the snapshot or the fingerprint
  approval_notification_mirror:         # v2: a second SINK, not a second approval source
    sink: hermes_bot_chat               # operator sends on HERMES_BOT_TOKEN, send-only
    decision_source: false              # invalid_approval_sources above is unchanged
```

- `assistant_dispatch_gate.requires.post_dispatch_audit`(`:187`) 주석에 "and one `task_registry` entry, origin AGENT" 추가.
- 범프 절차(1.4.0의 8c1cb02 그대로): YAML 헤더 `:3`, 리터럴 `scripts/validate_permission_approval_contracts.py:31·203·781`, `examples/**` 바인딩 17개, `tests/fixtures/**` ~40개, `rebuild_bundle`(`scripts/validate_i0_5_read_only_runtime.py:65`, CLI 없음·CRLF 정규화 해시)로 번들 2개, `docs/BUILD_HISTORY.md`. **PENDING 승인 0건 시점에 원자적으로.** 새 조문의 핵심 줄은 `require_doc_tokens`(`:777-790`)에 토큰으로 추가해야 실존이 게이트된다.

## Hermes 쪽 (레포 밖, PR7 배포 뒤)

- **단일 소켓 클라이언트 모듈** `D/mcp/thomas_door_client.py`: 4벌의 소켓 블록(`D/read_bridge_mcp.py:62-96` 등)을 하나로. 파라미터: 소켓 경로, 타임아웃(20/20/280/180), 문별 UNAVAILABLE 지시문 표. `json.dumps(ensure_ascii=False)`로 통일(한글 프레임 절반). 수신 술어는 개행 포함 청크에서 중단(3벌 방식).
- **v2 프레임 송신**: `proto: 2`, `client_id`(DM `hermes:dm`, cron `hermes:cron:<job>`, 위임 `hermes:delegate`), `request_id`(uuid4, 재시도에 재사용 — dispatch와 switch enable에만).
- **응답 처리**: `data`가 있으면 구조 렌더, 없으면 `reply` — `_stamp`는 두 경로 모두에(08-10 사건 방지 규칙 유지). `replayed` 분기 필수 — 지금 DONE 렌더에 replay 응답을 넣으면 `actor=None task=None` 빈 본문이 된다(`D/dispatch_bridge_mcp.py:112-116`). `REQUEST_IN_FLIGHT`는 "진행 중, tasks로 확인"으로.
- **switch shim의 `"scope" in reason` 특례 제거**(`D/switch_bridge_mcp.py:189-196`): 서버의 미허용 키 거절 문구가 항상 `sorted(_ALLOWED_KEYS)`를 인용해 `scope`를 포함하므로 지금도 모든 미지 키 거절이 "구형 런타임"으로 오보된다.
- **새 도구 5개**: `schedules`, `scheduler_events(limit)`, `heartbeat`, `approval_status(approval_id)`, `task_status(registry_entry_id)`; `task_result` 파라미터명을 `registry_entry_id`로. `STARTED_BUT_SLOW` 문구를 "완료됐을 수 있음 — `task_status`로 확인, 재시도는 같은 request_id로만"으로 교체.
- 승인 안내 문구(`D/switch_bridge_mcp.py:175-182`): 봇 id 리터럴을 상수로, "알림은 이 창에도 오지만 승인은 관제봇에서"로.
- `tools.tool_search.enabled: 'off'`라 도구 docstring이 매 턴 실린다 — 규범 문구는 SOUL.md로 옮기고 docstring은 짧게.
- shim 단위테스트(프레임 dict·렌더 분기; 소켓 I/O 분리) — 지금 4벌에 테스트가 없다.
- SOUL/cron/kanban(결정 ③·Q12·Q13): cron `enabled_toolsets`에서 thomas-switch 제외, `dispatch_in_gateway: false`, `scheduler_events` 폴링 ≥ 60 s.

## 검증 계획

- PR마다: 파일 단위 pytest(전체 스위트는 이 호스트에서 OOM), `validate_static_integrity`, Active Gate, `build_diagnostic_code_index --check`.
- 배포마다: candidate 이미지 안에서 **호출로** 단언(이름 매칭 금지, `CLAUDE.md`), `up -d` 뒤 hermes uid 10000 왕복(v1 프레임과 v2 프레임 둘 다), uid 0 거부 유지.
- shim v2 배포 뒤: `stop_trading`/`resume_runtime_only` 드릴 1회(`docs/RUNBOOK_APPROVAL_PATH_DRILL.md`) — 승인 미러링과 replay 경로를 같은 드릴에서.

## 이 기록이 하지 않는 것

- 취소·비동기 핸들·도메인 부분정지·`development` kind·API 서버·kanban — 결정 기록 §4의 전환 조건이 채워질 때까지.
- 문 프로세스의 요청 로그·하트비트(관측 공백 G2)는 별건.
- 정책 파일·스키마 enum 편집은 §정책 1.5.0과 D-1로 넘긴다.
- `client_id`로 권한을 나누는 것 — 절대.
