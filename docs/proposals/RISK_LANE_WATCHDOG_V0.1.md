# 제안: 리스크 레인 워치독 V0.1 — 멈춘 risk fire는 프로세스 재시작으로 끝난다

**상태:** DECIDED 2026-09-29 — 타임아웃 시 프로세스 재시작, 마감값 600/120/120 s(Thomas). 구현 PR 두 개(§7) 진행 중.
**참고:** 외부 개선 지시문(2026-09-28) 4항. 재시작 여부만 Thomas가 정했고, 마감 값·BUSY 표기·증거 기록 방식은 이 문서의 권고다.

## 1. 문제

리스크 레인(`thomas-scheduler`, `tick --lane risk`)은 `crypto_pipeline`·`crypto_breaker_watch`·`crypto_route_watch`를
**한 프로세스에서 순서대로** 돈다. 한 fire가 멈추면:

- 같은 프로세스의 breaker watch와 route watch도 멈춘다. 손실 브레이커와 경로 감시가 조용히 선다.
- 하트비트는 pass마다 한 번 찍히므로 300초(`STALE_FLOOR_SECONDS`) 뒤 STALE이 되고, 헬스체크(60초×3)가 unhealthy를 낸다.
- 그러나 **`restart: unless-stopped`는 unhealthy에 반응하지 않는다**(런북 §3). 회복은 사람 몫이고,
  신호는 host `health_watch.sh`의 unhealthy 알림(두 번 관측, 10분 간격 → 최대 약 20분)뿐이다.
- 워커로 위임하는 fire에는 `WORKER_DEADLINE_SECONDS=900` 같은 마감이 있지만, 그건 소켓 한 번의 마감이지
  **fire 전체의 마감이 아니다.** 벤더 호출·잠금 대기·파일 I/O 어디서든 멈출 수 있다.

## 2. 측정 (2026-09-29, `scheduler_events.jsonl`의 `duration_ms`, 전 기간)

| kind | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| `crypto_pipeline` | 851 | 30.6 s | 49.1 s | 68.1 s | 122.5 s |
| `crypto_breaker_watch` | 213 | 0.4 s | 1.1 s | 2.7 s | 3.2 s |
| `crypto_route_watch` | 852 | 0.3 s | 0.4 s | 0.5 s | 0.8 s |

`crypto_pipeline` 주기는 900초다. 유지보수 레인의 긴 fire(`candle_archive` 최대 155 s, `crypto_forward_cohort`
162 s, `crypto_factory` 125 s)는 **다른 프로세스**라 이 워치독의 대상이 아니다.

## 3. 설계

### 3.1 마감

kind별 고정 마감, 코드 상수:

| kind | 마감 | 근거 |
|---|---|---|
| `crypto_pipeline` | **600 s** | 관측 최대의 약 5배, 주기(900 s)보다 짧다 — 다음 발화 전에 끝난다 |
| `crypto_breaker_watch` | **120 s** | 관측 최대 3.2 s. 넉넉하되 브레이커가 2분 넘게 서지 않게 |
| `crypto_route_watch` | **120 s** | 관측 최대 0.8 s |

마감은 **"멈췄다"의 판정**이지 성능 목표가 아니다. 느린 날의 정상 fire를 죽이지 않는 것이 먼저다.

### 3.2 BUSY 하트비트 — 긴 정상 fire와 죽은 레인을 구분

fire 시작 직전에 하트비트 레코드에 `busy`를 쓰고, 끝나면 지운다(기존 tmp+replace 쓰기 그대로):

```json
{"service": "scheduler-risk", "heartbeat_at": "…", "interval_seconds": 30, "pid": 7,
 "busy": {"schedule_id": "schedule_…", "schedule_run_id": "srun_…", "kind": "crypto_pipeline",
          "started_at": "…", "deadline_at": "…"}}
```

`heartbeat_cli`의 판정:

| 상태 | 판정 |
|---|---|
| `busy` 없음, 마지막 pass가 한도 안 | FRESH (지금과 같음) |
| `busy` 있음, 지금 < `deadline_at` | **FRESH (BUSY)** — 오래 걸리는 정상 fire는 죽은 레인처럼 보이지 않는다 |
| `busy` 있음, 지금 ≥ `deadline_at` | **STALE (OVERRUN)** — 워치독이 곧 끝낼 fire |
| `busy` 없음, 마지막 pass가 한도 밖 | STALE (지금과 같음) — fire 밖에서 루프가 멈춘 경우 |

유지보수 레인은 표기하지 않는다(구현 때 정정). 마감이 없는 kind에 넉넉한 마감을 붙이면, 지금 300 s에
잡히는 유지보수 레인 멈춤을 오히려 늦게 잡는다.

### 3.3 마감 초과 시 — 2단 워치독

1. **Python 감시 스레드**(fire마다 무장, 끝나면 해제). 마감에 도달하면:
   1. `faulthandler.dump_traceback(all_threads=True)`로 모든 스레드의 스택을 stderr(docker 로그)에 남긴다.
   2. **진단 기록** `heartbeats/scheduler-risk.watchdog.json`을 tmp+replace로 쓴다:
      `schedule_run_id`, kind, `started_at`, `deadline_at`, `overrun_at`, pid, 스택 요약.
      거버넌스 레코드가 아니라 진단 파일이다 — 닫힌 스키마·원장을 새로 만들지 않는다.
   3. `os._exit(70)`. 도커의 `restart: unless-stopped`가 컨테이너를 다시 띄운다.
   - 2의 쓰기가 실패해도(디스크·권한) **종료는 한다.** 기록 실패가 멈춘 레인을 살려 두는 이유가 되면 안 된다.
     실패는 stderr에 한 줄 남긴다.
2. **C 수준 백스톱:** fire 무장 때 `faulthandler.dump_traceback_later(마감 + 30 s, exit=True)`도 건다.
   GIL을 쥔 채 멈춘 C 확장처럼 Python 스레드가 돌 수 없는 경우에도, faulthandler의 C 스레드가 스택을 찍고
   프로세스를 끝낸다. 정상 종료 시 `cancel_dump_traceback_later()`.

감시 대상은 **fire 한 번**이다. pass 사이의 대기(`--interval-seconds 30`)와 기동은 무장하지 않는다.

### 3.4 재시작 후

- 기존 `reconcile_stale_running`이 기동 시 짝 없는 `started`에 `abandoned_mid_run`을 붙인다
  (09-26 16:58 배포 때 실제로 동작 확인). 새 스키마·새 action이 필요 없다.
- 기동 시 `scheduler-risk.watchdog.json`이 있으면 **operator 알림을 보낸다**(기존 `alerter`):
  "리스크 레인 워치독이 `<kind>` `<run_id>`를 `<started>`부터 `<deadline>`까지 기다린 뒤 재시작시켰습니다". 보낸 뒤
  파일을 `…watchdog.<stamp>.json`으로 옮겨 한 번만 알린다. 알림은 새로 뜬 건강한 프로세스가 보낸다 —
  멈춘 프로세스에게 네트워크 호출을 맡기지 않기 위해서다.
- 도커 `RestartCount`가 오르므로 host `health_watch.sh`의 **crash loop** 규칙이 대기 없이 알린다(런북 §3).
  같은 fire가 매번 멈추면 재시작이 반복되고, 그 반복 자체가 알림 신호다.

## 4. 안전 경계 — 끊긴 쓰기에 대해 정직하게

`os._exit`는 쓰기 도중에도 끝낼 수 있다. 막을 수 없고, 막는 척하지 않는다. 대신:

- 마감은 관측 최대의 5배라, 끊기는 fire는 **쓰는 중이 아니라 기다리는 중**(네트워크·잠금)일 가능성이 압도적이다.
- JSONL 저장소는 끊긴 마지막 줄을 읽기에서 드러낸다(#891). 해시 체인 원장은 끊긴 줄을 검증 실패로 드러낸다.
  즉 끊긴 쓰기는 **조용히 넘어가지 않고 보인다.**
- 원자적 교체 파일(tmp+replace: 하트비트·포지션 북 등)은 교체 전에 끝나면 이전 판이 남는다 — 반쯤 쓴 파일이 권한을 갖지 않는다.
- **거래소에 걸린 손절·브래킷은 프로세스와 무관하게 거래소에 남는다.** 재시작은 주문을 취소하지 않고,
  열린 포지션의 청산 경로는 실행 단계와 무관하다(`EXECUTION_STAGE_V0.1.md` §1). 다음 사이클의 대사(reconciliation)가
  장부와 거래소를 맞춘다.
- 주문 전송 직후에 끊기면 "보냈는데 장부에 없음"이 된다. 이건 지금도 전원 차단·OOM·배포로 생길 수 있는 경우이고,
  다음 사이클의 대사가 그 심볼의 신규 진입을 거부한다(`live_leg` 주석). 워치독이 새로 만드는 위험이 아니라
  기존 복구 경로에 한 번 더 들어가는 것이다.
- 실행 권한·단계·주문 경로는 바꾸지 않는다. 워치독은 끝내기만 한다.

## 5. 테스트 (구현 PR에서, 변이 확인 포함)

- 정상 완료: 마감 전에 끝난 fire는 `busy`가 지워지고, 감시 스레드·faulthandler 타이머가 해제된다.
- 예외로 끝난 fire(L3a 경로)도 `busy`를 지우고 타이머를 해제한다 — 해제는 `finally`에 둔다. 그러지 않으면 쉬고 있는
  루프가 다음 fire 전까지 FRESH(BUSY)로 읽히고, 해제되지 않은 타이머가 정상 루프를 죽인다.
- 마감 초과: 가짜 fire가 잠들면(마감을 짧게 패치) 진단 파일이 쓰이고 종료 경로가 호출된다(`os._exit`는 주입해서 가로챈다).
- BUSY 하트비트: 마감 전에는 오래된 pass 스탬프여도 FRESH(BUSY), 마감 뒤에는 STALE(OVERRUN).
- STALE 하트비트: `busy` 없이 오래된 스탬프는 지금처럼 STALE.
- 증거 기록 실패: 진단 파일 쓰기가 예외를 내도 종료 경로는 호출된다.
- 재시작 후 회복: 진단 파일이 있으면 알림 한 번, 파일 이동, 두 번째 기동은 조용하다. 짝 없는 `started`는 `abandoned_mid_run`.
- 변이: 종료 호출 제거·`busy` 판정 제거·"기록 실패 시에도 종료" 제거가 각각 테스트를 깨야 한다.
- 실제 프로세스 1건: 하위 프로세스로 짧은 마감의 레인을 띄워 멈춘 fire를 넣고, 종료 코드 70과 진단 파일을 확인.

## 6. 하지 않는 것

- 유지보수 레인 종료·표기: 긴 fire가 정상이고, 멈춰도 리스크를 세우지 않는다. 멈춤은 기존 pass-age 규칙(300 s)이 잡는다.
- unhealthy 시 도커 재시작(autoheal 등): 런북 §3이 "별도 결정"이라 한 것 그대로 둔다. 워치독은 프로세스 안에서
  fire를 아는 쪽이 판정한다 — 하트비트 파일 나이만 보는 바깥 재시작은 긴 정상 fire를 죽인다.
- 워커 위임 마감(`WORKER_DEADLINE_SECONDS`) 변경.
- 새 거버넌스 레코드·스키마. 진단은 파일, 결말은 기존 `abandoned_mid_run`.

## 7. 구현 순서

1. **PR-1 BUSY 하트비트** — `heartbeat.write_heartbeat(busy=…)`, `heartbeat_cli` 판정, 두 레인이 fire 전후로 표기.
   동작 변화는 판정뿐(종료 없음). 배포해 BUSY가 실제로 찍히는지 하루 본다.
2. **PR-2 워치독** — 감시 스레드 + faulthandler 백스톱 + 진단 파일 + 기동 시 알림. 리스크 레인에만 무장.
   배포는 발화 창을 피해서, 배포 뒤 첫 `crypto_pipeline` 발화 확인.

## 8. 결정

- **마감 값**(§3.1: 600 s / 120 s / 120 s): Thomas 2026-09-29 "그대로 좋아".
- **알림 채널**(§3.4): 별도 지시 없음 — 기존 operator 관제 채널을 쓴다(권고 기본값).
