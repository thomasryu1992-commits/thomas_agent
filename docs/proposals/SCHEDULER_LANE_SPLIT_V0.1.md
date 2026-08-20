# 제안: 스케줄러 레인 분리 V0.1 — risk kind는 어떤 fire 뒤에도 줄 서지 않는다 (APPROVED v0.2)

> **상태: 승인 — Thomas 2026-08-19, §8의 D1·D2·D3 전부. PR-A 구현: #733.**
>
> **v0.2 (2026-08-19) — PR-A 감사 결과를 반영하고, v0.1이 몰랐던 지형 하나를 정정한다.**
> v0.1은 §2에서 "ablation fire 분할은 factory만 고치고, 다음에 길어질 kind마다 같은
> 수술을 반복하게 된다"고 썼는데, 그 수술은 이미 별도 상신으로 승인·병합되어 있었다:
> `FACTORY_FIRE_PROCESS_SEPARATION_V0.1` (#705 처방, #726으로 2026-08-17 병합) — factory
> **compute**를 fork된 자식으로 옮긴다. 그리고 그 다음 날(08-18) `crypto_pipeline`은
> 여전히 1,181초 늦었다 — 부모가 의도적으로 유지한 **in-pass fetch(~360초)** 뒤에서.
> null_control도 store-first 조기종료를 따로 받았다. 즉 kind별 수술 두 건이 실제로
> 진행되었고, 그 후에도 지연이 남았다는 것이 §2의 클래스 논거의 실증이다. 상세는
> §1.1(v0.2 추가)과 §5.3의 감사 확정.
>
> 사실 확인 기준: 2026-08-19, 라이브 원장(`runtime_ledger/scheduler_events.jsonl` + 아카이브
> 전체)을 직접 재측정. 코드 사실(인용한 상수·`claim_due`·`rotate_file`의 잠금)은
> `origin/main` = `3f3c7a0` (2026-08-19)에서 재확인함.
> 부모 기록: `docs/REMAINING_WORK.md`의 "Splitting the scheduler into risk / research /
> maintenance processes — measured 2026-08-06, not taken" 항목. 이 문서는 그 항목이
> "재개 조건 충족 — 무엇을, 할지 말지는 Thomas 결정"으로 끝낸 지점에서 시작한다.

---

## 0. 한 줄

단일 tick 프로세스를 **RISK_KINDS 레인과 MAINTENANCE_KINDS 레인 두 프로세스로** 나눈다 —
경계는 이미 코드에 존재하고 테스트로 고정된 그 분할을 그대로 쓰고, 리뷰가 요구했던
3분할(risk/research/maintenance)은 여전히 채택하지 않는다.

## 1. 문제 — 측정된 사실

2026-08-06의 보류는 "budget은 fire의 **시작**만 막고 **지속시간**은 못 막는다"를 알려진
노출로 명시했고, 당시 15일의 원장은 그 노출이 발현된 적 없다고 말했다. 이제는 발현이
일상이다. `crypto_pipeline`(RISK_KINDS, 캐던스 900초)의 960초 초과 갭, 2026-08-15 → 08-18:

| 날짜 | 갭 | 귀속 (fire 종료 시각과 초 단위 일치) |
|---|---|---|
| 08-15 08:19 | 1,225s | `crypto_factory` ablation fire 402s (#704 이후의 conjunction 비싼 날) |
| 08-16 08:18 | 1,161s | `crypto_factory` 337s |
| 08-17 04:48 | 1,132s | **`crypto_null_control` 3연속** — 234s/259s/257s, 각 pass가 하나씩 시작 |
| 08-17 08:19 | 1,201s | `crypto_factory` 384s |
| 08-18 08:18 | 1,181s | factory fire의 **in-pass fetch ~360s** (v0.2 정정 — §1.1; v0.1은 "원장에 없는 작업"으로만 적었다) |

나흘 연속, 하루 두 번인 날도 있다. 08-19는 factory가 93s/44s로 빨라 깨끗했다 —
문서가 예측한 대로 conjunction mix에 따라 요동칠 뿐, 바닥이 오른 것은 아니다.

세 가지가 2026-08-15의 기록에서 더 나아갔다:

1. **메커니즘이 둘이 되었다.** 08-17 04:48은 factory가 아니라 `crypto_null_control` —
   유지보수 kind — 이 4분대로 길어진 결과다. 같은 날 null_control 이월 행 129건이
   04:35–05:09 사이 캐스케이드로 해소됐다. "#704의 ablation fire 하나가 길다"가 아니라
   **어떤 kind든 길어지면 같은 실패를 낸다** — 단일 루프의 클래스 속성이다.
2. **duration_ms 회계 밖의 벽시계 시간이 존재한다.** 08-18의 factory run은 `started`
   08:12:48 → `fired` 08:20:48인데 `duration_ms`는 119,733 — 시작 전 pass가 이미 360s를
   쓴 상태였고 그 6분은 어떤 fire 행에도 없다. **duration 기반의 budget 개선은 이 시간을
   보지 못한다.**
3. **부모 항목의 "미귀속" 하나가 귀속됐다.** 08-15 심야 1,806s 갭의 창 안에는 02:14:01
   `failed:UNEXPECTED:RemoteDisconnected` 행이 있다 — 정시 발화한 시도의 네트워크 실패,
   스케줄러 지연 아님. (부모 항목 갱신 시 반영할 것.)

비용의 크기는 바뀌지 않았다: 보호 브래킷은 거래소에 상주하므로 각 사건은 진입·장부
경로의 ~4–5분 지연이지 무방비 포지션이 아니다. 바뀐 것은 **빈도(매일)와 폭(두 kind)**이다.

### 1.1 (v0.2) 08-18의 귀속 — "무기록 360s"는 factory의 in-pass fetch였다

두 가지가 v0.1의 읽기를 정정한다. 첫째, `fired` 행의 `created_at`은 fire의 시각이 아니라
**pass의 `now` 스탬프**다 — 한 pass의 모든 행이 같은 시각을 공유하며, `duration_ms`만이
실측이다. 둘째, 08-18의 이미지에는 #726(factory 자식 분리, 08-17 10:31Z 병합)이 이미
실려 있었다: `started` 08:12:48(claim) → 자식 spawn → 다음 pass들이 collect, `fired` 행
08:20:48에 `duration_ms=119,733`(자식 벽시계). 그러면 남는 조각이 정확히 맞는다 —
sibling deferral 행의 `pass_elapsed_ms: 360496`은 **부모가 의도적으로 유지한 fetch 단계**
(#705 §3: "boundary = the parent keeps the fetch")가 pass 안에서 소모한 ~6분이고, 그것이
어느 fire의 `duration_ms`에도 잡히지 않는 이유다. `crypto_pipeline`은 그 fetch가 끝난
08:18:49에 발화했다.

**이 귀속이 결정에 주는 것:** 자식 분리가 배포된 다음 날에도 risk kind는 6분 늦었다.
compute를 옮겨도 fetch가 남고, fetch를 또 옮기면 다음 긴 단계가 남는다 — 단일 루프
안에서의 수술은 항상 잔여를 남기며, 레인 분리는 잔여가 어디에 있든 risk 캐던스에서
그것을 통째로 치운다.

2026-08-06 보류의 논리는 절반이 여전히 옳다: research와 maintenance를 서로 나눌 근거는
그때도 지금도 없다 — 서로가 서로를 늦춰서 잃는 것이 없기 때문이다. 무너진 것은 나머지
절반, "risk kind가 그 뒤에 줄 서도 된다"이다. 그러므로 경계는 하나만 긋는다:
**돈 경로의 캐던스를 지켜야 하는 kind들과, 늦어도 되는 나머지 전부.**

부모 항목의 세 후보 중 나머지 둘이 탈락하는 이유:

- **duration-aware budget** — budget은 구조적으로 시작만 막을 수 있고(§1의 회계 구멍이
  그 한계를 이중으로 보여준다), 지속시간을 막으려면 선점(preemption)이 필요한데 그것은
  이미 기각된 방향이다(중단된 factory/archive 쓰기 = 찢어진 레코드).
- **ablation fire 분할** — factory만 고친다. 08-17의 null_control 사례가 그대로 남고,
  다음에 길어질 kind마다 같은 수술을 반복하게 된다. *(v0.2: 이 예측은 실측이 되었다 —
  factory는 #726으로, null_control은 store-first 조기종료로 각각 수술을 받았고, 그
  다음 날에도 08-18의 fetch 잔여 지연이 남았다. §1.1.)*

프로세스 분리는 클래스 전체를 닫는다: 어떤 kind의 fire도 `crypto_pipeline` 앞에 설 수
없는 것이 **배치의 속성**이 된다.

## 3. 무엇을 포기하지 않아야 하는가 (불변식)

1. **at-most-once.** `claim_due`는 잠금 아래 재읽기 후 fire 전에 `next_run_at`을
   전진시킨다 — 두 번째 claimant는 due를 찾지 못한다. 이 속성은 이미 프로세스 간
   잠금이며(compose 헤더가 명시), 레인 분리는 여기에 기대되, 추가로 두 레인의 kind
   집합이 서로소이므로 경합 자체가 구조적으로 사라진다.
2. **kill 의미론.** 두 레인 모두 control state를 **각 fire 직전** 재읽는다 — 지금과
   동일. kill이 두 프로세스에 닿는 시점이 달라질 수 있으나, 각 레인 안에서의 보장은
   지금의 단일 루프와 같거나 낫다(레인이 짧아진 만큼 재읽기 간격도 짧아진다).
3. **fire는 중단하지 않는다.** 이 제안은 선점을 추가하지 않는다. 보장은 여전히
   "시작을 통제"이며, 달라지는 것은 risk 레인에서 통제할 시작이 risk kind 자신뿐이라는
   점이다.
4. **레인당 tick 프로세스는 최대 1개.** compose 헤더의 "state volume당 스케줄러 1개"
   계약은 "레인당 1개"로 개정되며, 완화가 아니라 정밀화다 — 병렬 crypto 워커는 여전히
   범위 밖이다.
5. **새 Contract/Schema 없음.** `scheduler_event.v0`는 그대로다. 레인은 kind에서
   유도되므로 행에 새 필드가 필요 없다. 경계 상수도 새로 만들지 않는다(§5.1).
6. **자격증명 방향은 CREDENTIAL_PLANE_SEPARATION과 같은 쪽으로만.** 분리로 인해 키가
   **퍼지는** 일은 없어야 한다 — 새 프로세스는 계정·주문·라이브 변수를 하나도 받지
   않는다(§4).

## 4. 목표 그림 — 두 레인

| | risk 레인 | maintenance 레인 |
|---|---|---|
| kind | `crypto_pipeline`, `crypto_breaker_watch`, `crypto_route_watch` (= `RISK_KINDS`) | `MAINTENANCE_KINDS` 전부 (analysis_task, memory_prune, crypto_factory, crypto_report, crypto_propose, crypto_data_review, ledger_rotate, candle_archive, crypto_null_control) |
| 서비스/컨테이너 | 기존 `scheduler` / `thomas-scheduler` **그대로** + `--lane risk` | 신설 `scheduler-maint` / `thomas-scheduler-maint` + `--lane maintenance` |
| env | 지금의 scheduler env 그대로 (Binance 계정·주문 키, `MVP_LIVE_*`, 시장·계정 피드, 텔레그램 알림) | 시장 데이터·`COINALYZE_API_KEY`·`MVP_CANDLE_ARCHIVE`·텔레그램 알림 등 **fire들이 실제 소비하는 것만**; `BINANCE_*`, `MVP_ACCOUNT_FEED`, `MVP_LIVE_*`는 **절대 없음** |
| heartbeat | `scheduler-risk` | `scheduler-maint` |

돈 경로 컨테이너(`thomas-scheduler`)를 risk 레인으로 **유지**하는 것이 배치의 요점이다:
키를 든 서비스는 이름도 env도 바뀌지 않고 command 한 줄만 는다. 움직이는 것은 키가
필요 없는 쪽이다 — CREDENTIAL_PLANE_SEPARATION Phase ②의 "돈을 옮기지 말고 나머지를
옮긴다"와 같은 방향이며, 부수 효과로 **6분짜리 fire를 도는 프로세스가 더 이상 Binance
키 옆에 있지 않게 된다.**

**(v0.2) maintenance 레인의 env 목록, PR-A 감사로 확정** — fire 분기(`_execute`)를
kind별로 역추적한 결과:

- **필요**: `MVP_MARKET_DATA` (factory·null_control의 캔들 수집),
  `MVP_LIQUIDATION_FEED` + `COINALYZE_API_KEY` (두 kind의 피드 leg),
  `MVP_CANDLE_ARCHIVE` (아카이브 자체 venue 축),
  `MVP_OPERATOR_CHANNEL` + `TELEGRAM_BOT_TOKEN` (발신 전용 알림).
- **없음**: `BINANCE_ACCOUNT_*`, `MVP_ACCOUNT_FEED`, `MVP_PAPER_TRADING`,
  `MVP_LIVE_*` 전부 — analysis_task·report·propose·data_review는 `pipeline-worker`로
  위임되어 로컬 소비가 없고, prune·rotate·null_control 측정은 로컬이다.

"maintenance 레인은 계정·주문·라이브 변수를 읽지 않는다"의 테스트 고정은 compose가
실제로 두 서비스를 갖는 PR-B에서, 그 compose 블록을 대상으로 한다.

## 5. 메커니즘 — 코드 변경

의도적으로 작다. 새 모듈 없음, 새 스키마 없음, 도메인 문 없음.

### 5.1 레인 경계는 이미 존재하는 분할을 재사용한다

`RISK_KINDS`와 `MAINTENANCE_KINDS`는 이미 `KINDS`를 정확히 분할하고, 그 분할은
테스트로 고정되어 있다(미분류 kind = 붉은 스위트). 레인 경계로 이 두 상수를 그대로
쓴다 — 새 kind가 생기면 지금과 똑같이 분류를 강제받고, 분류가 곧 레인 배정이 된다.
새 상수·새 판단 지점이 생기지 않는 것이 이 재사용의 가치다.

변경 목록:

1. **`run_due(..., kinds: frozenset[str] | None = None)`** — `None`이면 지금과 동일
   (전체). 값이 있으면 due 필터에 `s.kind in kinds` 한 줄 추가. 정렬·budget·기록 로직은
   건드리지 않는다.
2. **`scheduler_cli tick --lane {risk,maintenance,all}`** — 기본 `all`(= 현행 동작,
   단일 프로세스 배치와 하위호환). 레인은 위 상수로 해석한다.
3. **heartbeat 레인화** — `heartbeat_cli`의 service 선택지에 레인별 이름을 추가하고
   각 컨테이너의 healthcheck가 자기 레인을 조회한다. 기존 `scheduler` 이름은
   `--lane all` 배치용으로 유지한다.
4. **abandoned/startup-gap 복구의 레인 스코프 — 이 제안의 유일한 진짜 함정.**
   `find_abandoned_runs`는 시작 시 unpaired `started`를 abandoned로 복구하는데, 두
   프로세스가 되면 **한 레인의 재시작이 다른 레인의 진행 중 fire를 abandoned로 오판**
   한다 (risk 레인 재시작 시 factory가 mid-fire면 거짓 `abandoned` 행 + 거짓 알림).
   `report_abandoned_runs`·`report_startup_gap` 모두 자기 레인의 kind만 보도록
   스코프하고, 이를 테스트로 고정한다: "레인 A의 시작 복구는 레인 B의 unpaired
   `started`를 건드리지 않는다."
5. **compose** — §4의 서비스 신설 + 헤더 계약 문구 개정("레인당 1개"). `scheduler`
   서비스는 command에 `--lane risk` 추가 외 무변경.

### 5.2 이미 안전해서 변경이 필요 없는 것 (이번에 확인함)

- **원장 append 경합** — 두 프로세스가 같은 `scheduler_events.jsonl`에 쓴다. jsonl
  계층의 per-file 잠금이 이미 프로세스 간이다.
- **`ledger_rotate` vs 다른 레인의 append** — `retention.rotate_file`은 "append가 잡는
  것과 같은 per-file 잠금 아래" 돌고, 아카이브 먼저·활성 파일 교체 나중 순서라 crash
  시 중복은 있어도 유실은 없다. maintenance 레인이 회전하는 동안 risk 레인이 쓰는
  시나리오는 이미 설계 안에 있다.
- **더블 클레임** — §3-1. 잠금 + 서로소 kind 집합의 이중 방어.

### 5.3 새로 생기는 인터리빙 — 지금까지는 단일 루프가 우연히 직렬화하던 것

단일 루프는 "factory가 도는 동안 pipeline이 pool을 읽는 일 없음"을 **약속한 적 없이
제공**해 왔다. 분리 후 겹칠 수 있는 쌍과 판단:

| 쌍 | 판단 |
|---|---|
| factory가 candidate를 mint하는 중 pipeline이 pool을 읽음 | append는 행 단위 원자 + 잠금 — reader는 일관된 prefix를 본다. PR-A에서 pool 읽기 경로가 "읽는 도중 재읽기"를 하지 않음을 확인하고 테스트로 고정 |
| null_control 백테스트가 읽는 store를 pipeline이 갱신 | null_control은 측정 전용("no candidates, no orders") — 어긋나도 측정 노이즈이지 주문 경로가 아니다. 확인만 하고 고정은 생략 |
| candle_archive 쓰기 중 pipeline이 캔들 읽기 | 아카이브는 자기 venue 축(`MVP_CANDLE_ARCHIVE`)을 갖고 pipeline은 `MVP_MARKET_DATA` 축 — 겹치는 파일이 있는지 PR-A에서 확인 |

이 표에 없는 쌍이 PR-A 감사에서 나오면 이 문서를 v0.2로 올려 기록한다 — 감사 없이
"괜찮을 것"으로 넘어가지 않는다.

**budget은 유물이 된다.** risk 레인에는 defer할 maintenance kind가 없어 발동 불가,
maintenance 레인에서는 지킬 risk kind가 없어 목적을 잃는다. 그래도 v0.1에서는
**건드리지 않는다** — deferral 행은 계속 쌓이고(무해), 제거는 분리가 안정된 뒤 원장
증거로 별도 결정한다(§8-D3). 한 PR에 구조 변경과 정책 제거를 같이 싣지 않는다.

## 6. 이 제안이 고치지 않는 것

- **risk 레인 자신의 긴/걸린 fire.** `crypto_pipeline`이 스스로 길어지면(현재 112–151s)
  뒤의 risk kind는 여전히 기다린다. 선점이 없는 한 남는 노출이며, 이 제안은 그것을
  더 좁히지 않는다 — 부모 항목의 "hung fire" 재개 조건은 risk 레인에 대해 그대로
  유효하게 남는다.
- **maintenance fire의 길이 자체.** factory ablation 6.5분, null_control 4분은 그대로다.
  분리로 그 비용이 **risk 캐던스에서 사라질 뿐**이다. 길이 자체가 문제가 되면(예: 아침
  버스트가 자기 레인 안에서 밀려 아카이브가 시간을 놓침) 그때 ablation fire 분할을
  다시 꺼낸다 — 그 후보는 기각이 아니라 후순위다.
- ~~**08-18의 무기록 360s.**~~ *(v0.2: 규명됨 — factory의 in-pass fetch, §1.1. fire
  **안의** 시간이었으므로 "fire 밖의 pass 작업" 우려는 해당 사례에선 성립하지 않았고,
  레인 분리가 이 시간도 risk 캐던스에서 치운다.)*

## 7. PR 시퀀스와 배포

**PR-A — 코드 (동작 무변경). ✅ 구현: #733 (`feat/a-tick-loop-can-run-one-lane`).**
§5의 1–4 + env·인터리빙 감사. 기본값 `--lane all`이므로 머지·배포되어도 아무것도
달라지지 않는다. 감사 결과는 이 v0.2에 반영됨(§1.1, §4). 구현이 §5에 더한 것 하나:
KIND_TASK의 registry 화해(reconcile)도 같은 소유권 논리로 레인 스코프했다 — risk 레인
재시작이 maintenance 레인의 진행 중 분석을 RUN_ABANDONED로 오판하는 네 번째 함정.
전체 스위트 5,257 통과, release gate PASS.

**PR-B — compose (배치 전환).** 서비스 신설 + `--lane risk` 부여 + 헤더 개정.
배포는 CLAUDE.md의 candidate-tag 절차 그대로. `compose up -d` 한 번으로 전환되며,
전환 창에서 구(무필터) 스케줄러와 신 maintenance 스케줄러가 잠시 겹쳐도 claim 잠금이
중복 발화를 막는다 — at-most-once는 전환 중에도 성립한다.

**수용 기준 (배포 후 14일, §1과 같은 쿼리로):**

1. maintenance-kind fire의 종료에 귀속되는 `crypto_pipeline` 960s 초과 갭 **0건**
   (재시작·네트워크 실패 귀속 건은 §1의 규약대로 제외).
2. 두 레인 모두 abandoned 오판 **0건** (특히 한쪽 재시작 직후 다른 쪽 in-flight fire).
3. factory·null_control·archive의 duration 분포가 분리 전과 동등 (동시성으로 인한
   저하 없음).

하나라도 깨지면 compose에서 `--lane` 두 줄을 걷어내는 것이 롤백의 전부다 — 코드
경로는 `all`이 그대로 살아 있다.

## 8. 요구되는 Thomas 결정

- **D1.** 2레인 분리를 채택하는가? (부모 항목의 문구로는: 보류했던 process separation을
  "fix"로 승격하는가 — 단, 3분할이 아니라 2분할로)
- **D2.** 배치 방향 승인 — 기존 `thomas-scheduler`가 risk 레인을 유지하고(키 이동 없음),
  신설 컨테이너가 maintenance를 가져가며 계정·주문·라이브 env를 받지 않는다는 §4의 표.
- **D3.** `MAINTENANCE_PASS_BUDGET_SECONDS`와 deferral 기록은 v0.1에서 존치하고, 제거는
  분리 안정화 후 원장 증거로 별도 상신한다 — 이 순서에 동의하는가?
