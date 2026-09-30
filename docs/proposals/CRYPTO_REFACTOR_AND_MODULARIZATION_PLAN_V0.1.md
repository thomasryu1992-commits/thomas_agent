# Crypto 리팩터·모듈화 계획 — 현재 구조 지도와 남은 단계

**상태:** DECIDED 2026-09-30 — D-1~D-5 권고대로(Thomas). 진행: PR-02·03(#1049), PR-04(#1059), PR-05(#1060), PR-07(#1064), PR-08(#1068, 리플레이 백테스트 분리), PR-09(#1071, 템플릿 공간 분리), PR-10(#1072, 생성기 분리), PR-11(#1075, 사이클 단계 함수 추출), PR-12(#1077, `live_order` 저장소 분리), PR-13(#1079, `live_leg` 결과 어휘·판독 함수 분리) 머지. `factory`는 5,477줄에서 1,763줄이, `live_order`는 2,041줄에서 769줄이, `live_leg`는 1,931줄에서 1,432줄이 됐다. PR-06 실측은 §K-1에 기록. PR-13까지는 candidate-1079로 배포해 첫 파이어를 관측했고(2026-09-30) 단계마다 독립 리뷰도 받았다. PR-13의 서명 테스트넷 사이클 1회는 아직 없다: 2026-09-30에 운영자가 실행했고, 실행 단계가 PAPER이고 테스트넷 opt-in이 꺼져 있어 가드가 거부했다(설계대로). SIGNED_TESTNET 전이 뒤에 다시 돌린다. PR-S3(#1082, 승격 문 CAS, 행동 변경: 풀이 바뀌었으면 설치 거부) 머지, candidate-1082로 배포·관측. 남은 구현: §S의 PR-14·15(R5).

**이 문서가 무엇인가:** "Crypto_AI_System — Architecture Refactor & Modularization Planning Task" 지시서(2026-09-30)에 대한
조사 보고서와 이행 계획이다. 기준은 `origin/main` `97b3dd98`(2026-09-30 03:41Z)이다. 런타임 코드·테스트·설정은 바꾸지 않았다.
주장마다 파일·함수·테스트를 적었고, 표기 규칙은 다음과 같다.

- **CURRENT** — 이 커밋의 코드와 테스트로 확인한 것.
- **PROPOSED** — 이 문서가 권하는 것. 아직 없다.
- **HISTORICAL** — 옛 문서나 옛 동작. 지금 `main`과 다르다.

이 지시서는 2026-09-15 "구조 개선 작업 지시서"(PR1 Execution Stage … PR7 분해)와 많이 겹친다. 그 작업은 PR1~PR7로
머지·배포됐다(`docs/BUILD_HISTORY.md`, 층 지도 시험 `tests/test_mvp_runtime_crypto_layers.py`). 그래서 이 문서는
구조를 처음부터 다시 그리지 않는다. 지시서 각 절을 **이미 강제되고 있는 장치**에 대응시키고, 실제로 남은 것만 계획한다.


## 결정 (Thomas 2026-09-30, 권고대로)

- **D-1 — 하위 패키지 이동 안 함.** 층 지도와 시험이 구조를 강제하고, 디렉터리는 평면으로 둔다(§L-1).
- **D-2 — PR-02·03 착수.** 둘 다 시험만 추가한다(§M-2).
- **D-3 — S-3을 행동 변경 과제로 받는다.** 승격 문의 설치에 CAS를 넣는 별도 PR(PR-S3)이다. 리팩터 PR과 섞지 않는다.
- **D-4 — S-1 유지.** `/kill`·`/pause`는 2026-09-15 결정대로 포지션 관리까지 멈춘다. PR-05가 이 현재 의미를 시험으로 고정한다.
- **D-5 — S-2는 PR-02의 명부로 먼저 덮는다.** 어댑터를 읽기 전용 프로토콜로 좁히는 일(PR-15, R5)은 실행 층 단계에서 한다.

남은 것: §S의 PR 순서대로 구현한다.

---

## A. 요약

**성숙도 — CURRENT.** 안전 통제는 높고, 모듈 경계는 중상이다.

- **층 방향이 시험으로 강제된다.** `crypto/`의 85개 모듈은 12개 층(foundation → governance → store → market →
  strategy → decision → risk → execution → reconciliation → outcome → orchestration → report) 중 정확히 하나에 속한다.
  함수 내부 import까지 읽어 상향 간선 0개·순환 0개를 고정한다(`EXCEPTIONS = {}`, `CYCLES = frozenset()`).
  지시서 §16의 의존 방향(Market → Strategy → Portfolio → Risk → Governance → Execution → Reconciliation → Analytics)과
  거의 같고, 차이는 두 군데다. governance가 맨 아래 잎이라는 점, portfolio 층이 따로 없다는 점(§B-3).
- **거래소 쓰기 코드는 어댑터 두 개뿐이다.** 메인넷 `live_execution.BinanceFuturesOrderAdapter`와 테스트넷
  `testnet_execution.BinanceTestnetOrderAdapter`만 서명 POST·DELETE를 보낸다. 둘 다 `safety_gate.select_env_gated`
  뒤에서만 만들어지고, 송신할 때마다 env를 다시 확인한다(§F).
- **자율 경로의 관문은 하나다.** 자율 주문 경로는 `cycle` → `live_route.run_live_leg` 하나이고 시험이 이를 고정한다
  (`test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`,
  `test_the_chokepoint_is_the_only_runtime_module_that_imports_the_executing_leg`).
- **단계 사다리는 지시서와 같다.** `execution_stage.ExecutionStage`가 READ_ONLY → SHADOW → PAPER → SIGNED_TESTNET →
  LIVE_AUTONOMOUS → LIVE_SCALED다. 진입 문은 목적(purpose)마다 요구 단계를 읽는다. 기록이 없으면 READ_ONLY이고,
  건너뛰기는 거부된다(`STAGE_SKIP_REFUSED`).

**주요 소견.** P0는 없다. 지시서 §31의 정지 조건 중 새로 발견된 것도 없다. 아래 세 가지는 보고 대상이다(자세한 내용은 §F-4).

- **S-1 (기존 결정 사항).** `/kill`·`/pause`는 스케줄러 발화를 통째로 버린다. 그동안 청산 정산·보호 재설치·시간 청산·
  대조가 모두 멈춘다. 긴급 청산도 `ACTIVE`+HARD에서만 돈다. 지시서의 "정지해도 축소 청산 가능"과 어긋나지만,
  2026-09-15 Thomas 결정("kill/pause 의미 유지 + Trading Soft Halt 신설")이다. 거래소에 걸린 브래킷 주문은 남아 있다.
- **S-2 (묵시적 능력).** 스케줄러의 거래 발화가 `venue_contract` 새로고침에서 메인넷 **주문 가능** 어댑터를 만든다.
  실제로 부르는 것은 `/order/test`와 GET뿐이고, 시험이 모듈 단위로 이를 고정한다. 그래도 능력이 쓰임보다 넓다.
- **S-3 (경합).** 승격 문(`scripts/promote_strategy_candidates.py`)은 풀을 잠금 밖에서 읽는다. 그리고 파일 전체를 잠금
  안에서 교체한다. 그 사이에 사이클의 두 writer(상태 전이, LIVE tier 해제)가 쓴 내용은 덮어써질 수 있다.

구조 부채는 네 가지다.

- report·outcome 층이 orchestration·egress 모듈을 상수 때문에 import한다(§E-2).
- 거래소 쓰기 호출자 명부가 모듈별 시험 여러 개에 흩어져 있고, `scripts/`는 층 시험 밖에 있다(§E-3).
- 연속 손실 계산이 두 벌이고 동작이 다르다(§K).
- 계통 사슬에 빈 고리가 있다(§J). 데이터·피처·신호·결정 id가 없고, 라이브 결과에서 사이클로 돌아가는 고리가 없다.
  라이브 결과는 수명주기·C6 보고로 가지 않는데, `live_settlement` 주석은 간다고 적는다.
- 5,477줄 `factory`, 2,000줄 안팎의 `live_order`·`live_readiness`·`live_route`·`live_leg`, 1,200줄 `cycle.run_crypto_cycle`처럼
  내부 분할이 남은 모듈이 있다.

**권고.** 폴더 재배치(지시서 §15 트리)는 하지 않는다. 층 지도가 같은 것을 이미 강제하고, 이동 비용은 크며 새로 막아 주는
것이 없다(§L). 먼저 **시험만 추가하는** PR 3개(거래소 쓰기 명부, 분석 층 egress 금지, 스크립트 능력 명부)를 한다. 그다음
분석·보고 쪽 상수 이동과 순수 로직 분할을 한다. 실행 경로(`live_route`·`live_leg`·`live_order`·`live_execution`)는 맨 마지막이다.

---

## B. 현재 구조 (CURRENT)

### B-1. 레인의 위치

- `runtime/mvp_runtime/crypto/`는 85개 모듈, 54,135줄이다. 코어(`runtime/mvp_runtime/*.py`)는 도메인 모듈을 모듈 수준에서
  import하지 않는다. 스케줄러·`domain_console`의 함수 내부 import로만 들어간다(`tests/test_mvp_runtime_domain_isolation.py`).
- 코어가 제공하는 관문은 세 가지다.
  - `safety_gate.select_env_gated`: env 옵트인이 있을 때만 능력 있는 구현을 만든다.
  - `approval_store`/`permission`: 승인 요청·결정·소비.
  - `control.ControlStore`: ACTIVE/PAUSED/KILLED, 무장, halt SOFT/HARD.
- 운영자 문은 `scripts/` 37개 파일이다(§F-3). 층 시험은 `crypto/` 안만 본다.

### B-2. 층 지도 (CURRENT, `tests/test_mvp_runtime_crypto_layers.py::LAYER`)

| 층 | 모듈 | 역할 |
|---|---|---|
| foundation | `state`, `candidate_identity`, `refresh_marks`, `indicators`, `vocabulary`, `order_identity` | 모든 층이 읽는 잎 |
| governance | `execution_stage`, `testnet_evidence`, `live_governance` | 행동하는 모든 층이 읽는 단계·증거·주문 기록 |
| store | `live_ledger`, `live_correction` | writer보다 아래 층이 읽는 기록의 읽기 경로. foundation만 읽는다(시험) |
| market | `market_data`, `candle_archive`, `oi_store`, `orderbook_store`, `positioning_store`, `features`, `account`, `account_store`, `live_filters`, `feed_assembly`, `cohort_retention` | 거래소·벤더가 말하는 것 |
| strategy | `strategy`, `strategy_artifact`, `cost`, `robustness`, `null_control`, `factory`, `proposer`, `proposer_cli`, `data_review`, `forward_book`, `forward_confirmation`, `lifecycle`, `distribution_gate`, `limit_entry`, `outcome_math`, `trade_plan`, `candidate_ranking`, `independence` | 전략의 정의·생성·판정 |
| decision | `paper`, `pool`, `pool_state`, `pool_admission`, `pool_transitions`, `promotion_backlog`, `live_tier`, `routing_marks`, `cooldown`, `promotion`, `retirement`, `forward_cohort`, `forward_cohort_null`, `forward_trial`, `judgement_fingerprint` | 어떤 전략이 도는지, 페이퍼 포지션 |
| risk | `guards`, `risk_limits`, `live_budget`, `live_allowance`, `pre_order_gate`, `breaker_watch`, `live_sizing` | 무엇을 걸 수 있는지 |
| execution | `live_order`, `live_execution`, `order_request`, `live_leg`, `live_entry`, `venue_contract`, `testnet_execution`, `live_position`, `live_settlement`, `probe` | 보내는 것, 그 준비, 장부, 결산 행 |
| reconciliation | `live_reconcile` | 거래소가 말하는 실제 결과와의 대조 |
| outcome | `live_pnl`, `feedback`, `digest`, `counterfactual`, `live_promotion` | 번 것과 그 의미 |
| orchestration | `cycle`, `live_route` | 단계를 차례로 부른다 |
| report | `dashboard`, `live_readiness`, `route_watch`, `tunables`, `strategy_funnel` | 모든 것을 읽고, 레인 안에서 아무도 import하지 않는다 |

### B-3. 지시서 개념과의 대응

| 지시서 개념 | 현재 위치 (CURRENT) | 메모 |
|---|---|---|
| Market Data / Data Lineage | `market_data`, `candle_archive`, 각 store, `feed_assembly` | 스냅샷 id가 없다(§J) |
| Features | `features.latest_feature_row`, `indicators` | 행의 `timestamp`는 봉 **시작** 시각 |
| ResearchSignal / Strategy | `strategy.StrategySpec`, `trade_plan.build_entry_plan`(paper 경유) | 신호 객체는 dict |
| Strategy Factory / Backtest / Walk Forward / Holdout | `factory`(생성·백테스트·홀드아웃·워크포워드 기록), `robustness`, `null_control`, `data_review`, `proposer` | `factory` 한 파일에 섞여 있다 |
| Forward Validation | `forward_book`, `forward_confirmation`, `forward_cohort`, `forward_cohort_null`, `forward_trial` | decision 층의 코호트는 풀과 분리 |
| Candidate Profiles / Promotion / Approval | `pool_state`(후보·풀 파일), `pool_admission`(게이트), `promotion`(승인 로스터), `strategy_artifact`, 코어 `approval_store`·`permission` | 승인은 코어 |
| Portfolio / Position Sizing | **전용 층 없음.** 전역 캡은 `live_order`(표식 재판정), `risk_limits`, `live_budget`, 슬롯은 `pool_admission`, 크기는 `live_sizing`, 독립성은 `independence`(보고) | §L-3 |
| Risk / PreOrderRiskGate / Hot-path Revalidation | `guards`, `risk_limits`, `live_budget`, `live_allowance`, `breaker_watch`, `pre_order_gate`, `live_route.reread_entry_facts`·`narrow_*` | 재검증은 orchestration 안 |
| Governance / Execution Stage | `execution_stage`, `testnet_evidence`, `live_governance`, 코어 `control` | |
| Paper / Testnet / Live Execution | `paper`(decision), `testnet_execution`, `live_entry`·`live_leg`·`live_order`·`live_execution`·`order_request` | |
| Venue Adapter | `live_execution`의 어댑터 두 개(드라이런·메인넷), `testnet_execution`의 두 개 | |
| Protective Orders | `live_leg.place_bracket_leg`·`cancel_bracket_legs`, `live_route._settle_or_protect` | |
| Position Management | `live_position`(장부), `live_leg.execute_live_exit`·`settle_venue_closed_position`, `live_route._time_exit_or_hold` | |
| Reconciliation | `live_reconcile.reconcile_positions`, `order_request.reconcile_order` | |
| Outcome Analytics / Feedback | `live_pnl`, `live_settlement`, `feedback`, `digest`, `counterfactual`, `lifecycle`(strategy) | |
| Scheduler / Watchdog / Monitoring / Operator State | 코어 `scheduler`·`fire_watchdog`·`heartbeat`, `breaker_watch`, `route_watch`, `live_readiness`, `dashboard`, 코어 `control` | |

**문서와의 불일치.**

- **HISTORICAL:** `live_execution`의 머리 주석은 "LP4 is the narrow, and only, code that can send an order"라고 한다.
  - CURRENT: 테스트넷 어댑터(`testnet_execution`, PR1d-1)도 서명 POST·DELETE를 보낸다.
  - CURRENT: 보호 레그는 `submit_and_reconcile`를 거치지 않고 `adapter.submit`을 직접 부른다(`live_leg.py:549`).
    보호 주문만 허용한다(`is_protective_request`, 아니면 `BRACKET_LEG_NOT_PROTECTIVE`로 보내지 않음).
- **HISTORICAL:** `live_route` 머리 주석은 "crypto/cycle.py imports this module and nothing else from the live stack"이라고 한다.
  시험의 허용 목록은 `{"live_route", "live_pnl"}`이다(`ENTRY_POINTS`, `test_mvp_runtime_crypto_live_execution.py:240`).
  `live_pnl`은 결과 읽기라 주문 경로가 아니다. 문장만 낡았다.
- **불일치 (단계 의미):** 지시서는 READ_ONLY를 "읽기만"으로 읽는다. CURRENT에서 단계는 **실주문 목적**만 가른다
  (`_REQUIRED_STAGE`: probe·autonomous·live_arm → LIVE_AUTONOMOUS, signed_testnet → SIGNED_TESTNET).
  - 페이퍼 거래와 연구 파이프라인은 단계를 읽지 않는다. 단계를 읽는 곳은 `live_order`, `live_route`, `live_readiness`,
    `promotion`, `testnet_execution`과 스크립트 4개뿐이다.
  - 그래서 READ_ONLY·SHADOW·PAPER는 런타임 동작이 같다.
  - LIVE_SCALED도 LIVE_AUTONOMOUS와 같게 취급된다(`permission.py:1198` "as LIVE_AUTONOMOUS").
  - 결함은 아니다. 이 리팩터에서 바꾸면 안 되는 의미론이다(지시서 §2).
- **불일치 (portfolio):** 지시서의 Strategy → Portfolio → Risk 흐름에서 portfolio 결정 단계는 CURRENT에 없다.
  라우터(`paper`의 라우팅, `pool_admission`의 문맥 캡)와 진입 문의 전역 캡 재판정(`live_order`의 표식)이 그 역할을 나눠 갖는다.

---

## C. 런타임 호출 그래프 (CURRENT)

### C-1. 자율 경로 — 스케줄러 발화 한 번

```
scheduler.run_due  (risk 레인 컨테이너 thomas-scheduler; RISK_KINDS 먼저)
│  control.load().execution_allowed == False  → 발화를 claim하고 버린다 (scheduler.py:2538)   ← S-1
└─ kind crypto_pipeline → cycle.run_pool_cycle            (열린 라이브 포지션의 문맥이 먼저, cycle.py:800)
   └─ 문맥마다 cycle.run_crypto_cycle                        (cycle.py:126)
      1  데이터    market_data 수집기(공개 GET/POST, `MARKET_DATA_ENV` 게이트) + feed_assembly.attach_feeds
                   + HTF·교차자산·횡단면·포지셔닝 (저하만, 차단 안 함)
      2  피처      features.latest_feature_row
      3  가드      pool.load_active_pool, risk_limits.resolve_risk_limits, guards.* (paper/live 두 판정)
      4  페이퍼    paper.run_paper_update  (전략 평가 → trade_plan → 페이퍼 포지션, PAPER_ENV 게이트)
      4b 관찰      counterfactual.run_counterfactual_update, forward_book.run_forward_book_update
      5  보고      feedback.run_paper_performance_report   (보고만)
      4c 허용치    live_allowance.evaluate_live_allowance → pool.disarm_live_tier   (좁히기만)
      4d 라이브    live_route.run_live_leg                   ← 유일한 자율 호출자 (시험 고정)
      5b 수명주기  lifecycle.run_lifecycle → pool.apply_status_decisions   (강등만, 자동 승격 없음)
   └─ 발화 끝: account_store 새로고침, venue_contract 새로고침(주문 키 어댑터로 /order/test·GET) ← S-2
```

### C-2. 라이브 레그 — `live_route._run_gated_live_leg` (live_route.py:400–824)

```
select_live_gate ─ MVP_LIVE_TRADING != real → DISABLED (계좌·소켓·결정 없음)
0  assert_not_foreign_root_run
1  사실 수집: resolve_execution_stage, control.trading_allowed, account.read_account(읽기 키 서명 GET),
   live_reconcile.reconcile_positions(장부 ↔ 거래소)
2  정산·보호 먼저 (단계·무장을 읽지 않음)
   _settle_or_protect → live_leg.settle_venue_closed_position | execute_live_exit | 브래킷 재설치
   _time_exit_or_hold → 시간 청산(reduceOnly 시장가)
   대조 불가·보호 없는 포지션·장부 불일치 → record["halt"] = True → 팬아웃 정지
3  진입 결정: live_entry.plan_live_entry (페이퍼 평가의 route 재사용, live_tier, live_budget, risk_limits,
   live_sizing, 쿨다운, 단계 ≥ LIVE_AUTONOMOUS)
3a 재조회: reread_entry_facts → narrow_guard_facts/narrow_entry_facts(좁히기만), verify_live_arm
3b 사전 주문 게이트: pre_order_gate.approved_profile → live_entry.gate_live_entry(스냅샷 봉인) → bind_intent
4  주문: live_leg.execute_live_entry
   → live_order.evaluate_live_order_guard (최종 가드)
   → live_governance 기록 → live_order.reserve_submission (일일 주문 수, 전송 전 잠금 예약) → 심볼 in-flight 표식
   → live_execution.submit_and_reconcile
       guard_verdict.approved 필수 · reduceOnly 아니면 pre_order_gate.verify_and_persist 필수
       → adapter.submit → control_refusal (HARD·PAUSED·KILLED면 노출 증가 주문 거부, 보호·청산은 상태도 안 읽음)
       → fetch_order로 대조 (RECONCILED만 포지션을 연다)
   → live_position 저장 → live_leg.place_bracket_leg (보호 주문만 adapter.submit)
```

### C-3. 운영자·비서 문 (CURRENT)

| 문 | 경로 | 거래소 효과 |
|---|---|---|
| `scripts/run_slippage_probe.py --fire` | 스크립트 안에서 `evaluate_live_order_guard` → `pre_order_gate` → `live_execution.submit_and_reconcile` → `live_leg.place_bracket_leg`/`execute_live_exit` | 메인넷 진입·보호·청산 (1회 probe) |
| `scripts/run_signed_testnet_cycle.py` | `testnet_execution.select_testnet_order_adapter`(`MVP_TESTNET_TRADING`) → `submit_and_reconcile` → `adapter.cancel_order` | 테스트넷 진입·보호·청산 |
| `scripts/emergency_close.py --request/--confirm` | `live_route.run_emergency_close` → `select_live_gate` → `live_leg.execute_live_exit` | 메인넷 reduceOnly 청산만 |
| `scripts/diagnose_bracket_leg.py` | `select_order_adapter` → `validate_order` | `/order/test`만 (시험 고정) |
| `scripts/list_resting_orders.py` | `select_order_adapter` → `open_orders`·`algo_open_orders` | 읽기만 (시험 고정: 취소 경로 없음) |
| `switch_bridge` (Hermes 비서) | `halt_trading`, `emergency_close` **요청만** | 없음 (Thomas 승인 뒤 스크립트가 실행) |

---

## D. 모듈 목록 (주요 모듈)

재무 영향 등급: CRITICAL = 실주문을 만들거나 막는다 / HIGH = 주문 여부·크기를 바꾼다 / MEDIUM = 무엇이 라우팅되는지 바꾼다 /
LOW = 보고·연구. 거래소 접근은 NONE / READ(공개) / SIGNED-READ / WRITE.

| 모듈 (층) | 책임 | 분류 | 권한 | 거래소 | 부작용 | 영향 |
|---|---|---|---|---|---|---|
| `live_execution` (execution) | 메인넷 어댑터, `select_order_adapter`, `control_refusal`, `submit_and_reconcile` | EXCHANGE_WRITE, RISK_DECISION | 송신 관문 | WRITE (POST order·algoOrder, DELETE, `/order/test`) | 사전 주문 스냅샷 기록 | CRITICAL |
| `testnet_execution` (execution) | 테스트넷 어댑터·가드 | EXCHANGE_WRITE | 테스트넷 송신 | WRITE (테스트넷) | 테스트넷 카운터 | HIGH |
| `live_leg` (execution) | 진입 실행, 보호 레그 설치·취소, 청산, 정산 | EXCHANGE_WRITE, STATE_MUTATION | 진입·보호·청산 실행 | WRITE (어댑터 경유) | 장부·원장·카운터·표식 | CRITICAL |
| `live_order` (execution) | 최종 가드, 예산 기반 한도, 카운터·브레이커·진입 표식 | RISK_DECISION, STATE_MUTATION | 최종 거부권 | NONE | 카운터·브레이커·표식 파일 | CRITICAL |
| `live_entry` (execution) | 라이브 진입 계획, 게이트 호출, 사실 좁히기 | RUNTIME_DECISION, RISK_DECISION | 진입 결정 | NONE | 없음(결정 dict) | CRITICAL |
| `order_request` (execution) | 요청 모양·정규화·대조 판정 | PURE | 없음 | NONE | 없음 (순수성 시험) | HIGH |
| `live_position` (execution) | 라이브 장부(심볼당 1행) | STATE_READ, STATE_MUTATION | 없음 | NONE | `live_positions/*.json` | HIGH |
| `probe` (execution) | probe 계획·배치 검사 | STATE_MUTATION, RISK_DECISION | 없음 (송신은 스크립트) | NONE | probe 계획 CAS | HIGH |
| `venue_contract` (execution) | 거래소 계약 감시 | EXCHANGE_READ, STATE_MUTATION | 진입 거부 가능 | SIGNED-READ + `/order/test` (주문 키) | 계약 기록·표식·알림 | HIGH |
| `pre_order_gate` (risk) | 순수 게이트, 봉인 스냅샷 저장소 | RISK_DECISION, STATE_MUTATION | 진입 거부 | NONE | `pre_order_risk_snapshots.jsonl` | CRITICAL |
| `guards` (risk) | 풀 전체 손실·연속 손실·낙폭 판정 | PURE, RISK_DECISION | 진입 거부 | NONE | 없음 | HIGH |
| `risk_limits`, `live_budget` (risk) | 등록 한도·예산 읽기·검증 | STATE_READ, RISK_DECISION | 한도 결정 | NONE | 운영자 스크립트만 쓴다 | HIGH |
| `live_allowance` (risk) | 계보별 LIVE 허용치 | PURE, RISK_DECISION | 무장 해제 트리거 | NONE | 없음 (해제는 `live_tier`) | HIGH |
| `live_sizing` (risk) | 주문 크기 | PURE | 없음 | NONE | 없음 | HIGH |
| `breaker_watch` (risk) | 브레이커 감시·알림 | SCHEDULER, READ_ONLY | 없음 | NONE | 표식·알림 | LOW |
| `execution_stage` (governance) | 단계 기록 읽기·전이 | STATE_READ, APPROVAL_DECISION | 단계 전이 (승인 소비) | NONE | `execution_stage.json` | CRITICAL |
| `live_governance` (governance) | 주문별 거버넌스 기록 | STATE_MUTATION | 없음 (기록) | NONE | 레코드 원장 | MEDIUM |
| `live_route` (orchestration) | 라이브 평면 전체, 긴급 청산 | RUNTIME_DECISION, EXCHANGE_WRITE(위임) | 자율 경로 관문 | 어댑터 경유 | 여럿 | CRITICAL |
| `cycle` (orchestration) | 문맥별 사이클, 팬아웃 | RUNTIME_DECISION, SCHEDULER | 없음 | READ (위임) | 레코드 | HIGH |
| `live_reconcile` (reconciliation) | 장부 ↔ 거래소 비교 | PURE | 진입 거부·halt 근거 | NONE | 없음 | HIGH |
| `live_pnl`, `live_settlement`, `live_ledger`, `live_correction` | 라이브 결과 원장·결산 행·읽기·정정 | STATE_MUTATION, ANALYTICS | 없음 | NONE (거래소 수치는 `account` 스냅샷에서 받는다) | `live_outcomes.jsonl`, 정정 | HIGH (브레이커 입력) |
| `account`, `account_store` (market) | 서명 계좌 읽기, 스냅샷 | EXCHANGE_READ | 없음 | SIGNED-READ (읽기 키) | 스냅샷 | HIGH |
| `market_data` 외 market 층 | 시세·파생·오더북 수집·보관 | EXCHANGE_READ, STATE_MUTATION | 없음 | READ | 보관 파일 | MEDIUM |
| `pool_state`, `pool_admission`, `pool_transitions`, `live_tier`, `promotion`, `retirement` (decision) | 풀 파일, 승격 게이트, 상태 전이, LIVE tier·해제, 승인 로스터, 은퇴 문 | STATE_MUTATION, APPROVAL_DECISION | 무엇이 라우팅·무장되는지 | NONE | 풀·후보 파일 | HIGH |
| `paper` (decision) | 페이퍼 포지션·라우팅 | STATE_MUTATION, RUNTIME_DECISION | 페이퍼만 | NONE | 페이퍼 저장소 | MEDIUM |
| `factory`, `proposer`, `robustness`, `null_control`, `data_review` (strategy) | 후보 생성·백테스트·검증 | RESEARCH_ONLY | 없음 (후보 append만) | READ (보관 캔들) | 후보 파일 | LOW |
| `forward_*` (strategy·decision) | 가상 forward 장부·코호트 | RESEARCH_ONLY, STATE_MUTATION | 없음 | NONE | 전용 저장소 | LOW |
| `feedback`, `digest`, `counterfactual` (outcome) | 성과 보고, 반사실 | ANALYTICS | 없음 ("reports, it never acts") | NONE | 반사실 장부 | LOW |
| `live_readiness`, `dashboard`, `route_watch`, `strategy_funnel`, `tunables` (report) | 보드·감시·색인 | READ_ONLY, ANALYTICS | 없음 | `live_readiness`는 SIGNED-READ | 표식·알림 | LOW |

`live_promotion`은 이름과 달리 승격하지 않는다. 2026-09-15에 제거된 카나리 문의 옛 기록을 읽기만 한다(CURRENT, 머리 주석).
이름이 오해를 부른다.

---

## E. 현재 의존 그래프

### E-1. 강제되는 것 (CURRENT)

| 규칙 | 강제하는 시험 |
|---|---|
| 모든 crypto 모듈은 층 하나, 상향 import 0, 순환 0 (함수 내부 import 포함) | `test_mvp_runtime_crypto_layers.py` |
| store는 foundation만 읽는다 | `test_the_store_reads_nothing_above_foundation` |
| 코어는 도메인 모듈을 로드하지 않는다. 도메인은 지정된 문으로만 들어간다 | `test_mvp_runtime_domain_isolation.py` |
| 자율 진입점(`cycle`·`scheduler`·`pipeline`·`operator`·`domain_console`)은 주문 경로를 직접 import하지 않는다 | `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module` |
| `live_leg`는 `runtime/`에서 `live_route`만 import한다 | `test_the_chokepoint_is_the_only_runtime_module_that_imports_the_executing_leg` |
| 거래소를 부르는 문은 주문 수를 센다 (probe·자율 leg·테스트넷) | `test_every_caller_of_the_venue_also_counts_the_order` |
| `order_request`는 네트워크·키 모듈을 import하지 않는다 | `test_mvp_runtime_crypto_live_execution.py`의 순수성 핀(PR7e-4) |
| `venue_contract`는 주문·취소를 부르지 않는다 | `test_the_module_imports_and_calls_nothing_that_can_place_or_cancel` |
| `list_resting_orders`에는 취소 경로가 없다 | `test_it_has_no_way_to_place_or_cancel`, `test_no_bulk_cancel_path_exists_in_the_module` |
| 게이트 env 옵트인은 시험에서 격리된다 | `test_the_suite_isolates_every_gate_opt_in_env_var` |

지시서 §8의 역의존 후보를 검증한 결과(CURRENT).

| 후보 | 결과 |
|---|---|
| execution → research | **없음.** strategy 층이 execution 아래라 반대 방향은 허용이다. 그 간선도 execution → strategy의 `cost`·`trade_plan` 같은 순수 모듈뿐이다 |
| execution → strategy factory | **없음.** `factory`를 import하는 execution 모듈이 없다 |
| risk → backtest | **없음.** risk는 `factory`를 import하지 않는다 |
| strategy → live execution | **없음.** 층 시험이 막는다 |
| analytics → runtime mutation | **좁히기만 있다.** 아래 표 참고 |
| feedback → execution | **없음.** `feedback`은 `paper`만 import하고 "reports, it never acts" |
| research → exchange adapter | **없음.** strategy 층은 execution을 import할 수 없다 |

자동으로 런타임을 바꾸는 피드백 경로는 두 개이고, 둘 다 좁히기만 한다(CURRENT).

| 경로 | 동작 |
|---|---|
| `lifecycle` → `pool_transitions.apply_status_decisions` | 강등(WARNING·PROBATION·SUSPENDED·ARCHIVED). SUSPENDED 이상은 종단이고, 복귀는 승격 문(승인)뿐이다. WARNING·PROBATION → PAPER_ACTIVE 복귀는 자동이지만 점유 집합 안의 라벨 이동이다(`lifecycle.py` 머리 주석) |
| `live_allowance` → `live_tier.disarm_live_tier` | LIVE tier 해제만. "can only take it away" |

### E-2. 문제 간선 (CURRENT, 층 규칙은 허용하지만 지시서 §22와 어긋남)

층 순서상 report·outcome은 execution보다 위라 execution을 import해도 층 시험을 통과한다. 지시서는 "analytics cannot import
exchange-write modules"를 요구한다.

| source → target | 가져가는 것 | 심각도 | 런타임 영향 | 권장 경계 |
|---|---|---|---|---|
| `live_readiness` (report) → `live_route` (orchestration) | `ACCOUNT_UNREADABLE`, `ROUTE_*` 상수, `verify_live_arm`, `halt_advice` | 중 | 없음 (호출은 읽기 판정뿐). import 시 egress 모듈이 로드된다 | 상수는 `vocabulary`로, `verify_live_arm`은 `live_tier`나 새 순수 모듈로 |
| `live_readiness` → `live_order` | 상수, `evaluate_live_order_guard`(판정만), 브레이커 상태 읽기 | 낮음 | 없음 | 판정 함수가 순수하면 허용 목록에 이름으로 둔다 |
| `live_readiness` → `cycle` | 사유 코드 상수 2개 | 낮음 | 없음 | `vocabulary`로 |
| `route_watch` (report) → `live_route` | `ROUTE_INCIDENT` | 낮음 | 없음 | `vocabulary`로 |
| `live_promotion` (outcome) → `live_execution` | `RECONCILED` (재수출) | 낮음 | 없음 | `order_request`에서 가져온다(값은 같은 객체). `scripts/run_slippage_probe.py`의 재수출 의존도 함께 |
| `tunables` (report) → `testnet_execution` | 테스트넷 한도 상수 2개 | 낮음 | 없음 | 송신하지 않는 `testnet_evidence`로 옮긴다(tunables 소유자 경로도 함께) |

`tunables` 쌍은 이 문서의 초판이 놓쳤고, PR-03의 시험이 찾았다. 송신 모듈(`live_execution`·`testnet_execution`·`live_leg`·`live_route`)을
import하는 쌍은 위 표에서 넷이고(`live_readiness → live_route`, `route_watch → live_route`, `live_promotion → live_execution`,
`tunables → testnet_execution`), PR-04(#1059)가 모두 없앤다. `live_readiness → live_order`·`→ cycle`은 송신 모듈이 아니라서
M-2b의 대상이 아니다.

### E-3. 층 시험이 보지 않는 곳 (CURRENT)

- **`scripts/`.** 37개 스크립트가 crypto를 import한다. 층 시험은 `crypto/`만 스캔한다.
  - `run_slippage_probe.py`(1,291줄)는 진입 가드·게이트·송신·보호·청산 조립을 스크립트 안에 둔다.
    execution 층 논리가 층 지도 밖에 있는 셈이다.
- **거래소 쓰기 호출자 명부가 하나로 모여 있지 않다.** 위 표의 핀은 모듈별이다.
  - "`adapter.submit`·`cancel_order`·`submit_and_reconcile`를 부르는 곳은 어디인가"를 한 번에 답하는 시험이 없다.
  - 지금 답은 `live_leg`(3곳), `live_execution`(1곳), 스크립트 2개다(§F-2).
  - 새 스크립트가 `select_order_adapter()`로 어댑터를 얻어 `submit`을 부르면, 주문 수 시험(`reserving` 목록)을 빼고는
    막는 것이 없다. 그 시험도 목록에 **있는** 문만 검사한다.

---

## F. 런타임 권한 지도

### F-1. 누가 무엇을 할 수 있는가 (CURRENT)

| 행위 | 권한자 | 경로·근거 |
|---|---|---|
| 신호 생성 | 풀의 전략 spec | `paper.run_paper_update`의 평가. 라이브는 이 route를 재사용한다(`live_route` 머리 주석 "route once") |
| 거래 승인(진입 허가) | 결정 체인 | `live_entry.plan_live_entry` → `pre_order_gate`(봉인) → `live_order.evaluate_live_order_guard` → `submit_and_reconcile`(`approved is True` 필수) |
| 거래 거부 | 모든 문 | 단계, 무장(`trading_allowed`), 예산·한도, 브레이커, 선택 데이터 건강, 신선도, 계약 감시, API 브레이커, 심볼 in-flight, 잔여 주문, 어댑터의 `control_refusal` |
| 노출 증가 | 자율 leg, probe `--fire` | 둘 다 `LIVE_AUTONOMOUS` 이상 단계, `MVP_LIVE_TRADING=real`, 등록 예산, 확인 문구, 봉인 스냅샷이 필요하다 |
| 주문 제출 | `adapter.submit` | `submit_and_reconcile`(진입·청산), `live_leg.place_bracket_leg`(보호만) |
| 주문 취소 | `adapter.cancel_order` | `live_leg.cancel_bracket_legs`(청산 확인 뒤), `run_signed_testnet_cycle`(테스트넷) |
| 포지션 청산 | `live_leg.execute_live_exit` | 자율 leg(보호 소실·시간 청산), probe, 긴급 청산. 청산 가드는 단계를 읽지 않는다(PR1b, 시험 고정) |
| 레버리지 변경 | **없음** | 레버리지 쓰기 엔드포인트를 부르는 코드가 없다. `venue_contract`가 5x 이하를 검사만 한다 |
| 단계 변경 | 운영자 | `scripts/register_execution_stage.py --request/--confirm`(승인 소비 필수), DEMOTE는 무승인·즉시·아래로만. 자동 전이 없음 |
| 승인 변경 | Thomas (승인 저장소) | `approval_store`. 승격 v5는 `[candidate_id, artifact]` 쌍에 결속 |
| 전략 승격·무장 | 운영자 | `scripts/promote_strategy_candidates.py`(승인 필수, LIVE는 `allow_unconfirmed_holdout` 금지) |
| 런타임 설정 변경 | 운영자 env | `MVP_LIVE_TRADING`, `MVP_TESTNET_TRADING`, 확인 문구, 수동 킬 env. 디스크 기록이 없다(2026-07-28 결정, `safety_gate` 시험 머리 주석) |
| 정지·해제 | Thomas(텔레그램 `/kill`·`/pause`·`/resume`·`/halt_trading`), 비서 | 비서는 `halt_trading`과 긴급 청산 **요청**만(`switch_bridge`, 정책 1.5.2+) |

### F-2. 거래소 쓰기에 닿는 모든 경로 (CURRENT)

| # | 함수 | 동사 | 호출자 | 분류 |
|---|---|---|---|---|
| W1 | `BinanceFuturesOrderAdapter.submit` (`live_execution.py:540`) | POST `/fapi/v1/order`·`/fapi/v1/algoOrder` | `submit_and_reconcile`, `live_leg.place_bracket_leg` | 관문 안 (env 게이트 + `control_refusal`) |
| W2 | `BinanceFuturesOrderAdapter.cancel_order` (`:743`) | DELETE | `live_leg.cancel_bracket_legs` | 관문 안. 청산 확인 뒤에만(시험 `test_the_cancel_happens_only_after_the_close_is_confirmed`) |
| W3 | `BinanceFuturesOrderAdapter.validate_order` (`:578`) | POST `/fapi/v1/order/test` | `venue_contract`, `diagnose_bracket_leg` | 주문을 만들지 않는 검증기 |
| W4 | `BinanceTestnetOrderAdapter.submit`·`cancel_order` (`testnet_execution.py:242,297`) | POST·DELETE (테스트넷 호스트) | `run_signed_testnet_cycle` | 테스트넷, 별도 env·키 |
| W5 | `submit_and_reconcile` (`live_execution.py:824`) | W1 경유 | `live_leg`(진입 `:820`, 청산 `:1156,:1412`), `run_slippage_probe:915`, `run_signed_testnet_cycle:235,315` | 공유 송신 루프 |

- 포지션 변경은 W1(진입·청산) 결과를 대조한 뒤 `live_position` 장부에 쓰는 것뿐이다.
- 보호 주문 생성은 W1을 `place_bracket_leg`로 부르는 것뿐이다.
- 연구·분석·factory·proposer에서 W1~W5로 가는 경로는 없다. 층 시험이 import 수준에서 막는다.
- **두 번째 자율 관문은 없다.** 운영자 문 셋(probe·테스트넷·긴급 청산)은 의도된 별도 문이다. 각자 단계·게이트·카운터를 거친다.

### F-3. 스크립트 능력 분류 (CURRENT, crypto를 import하는 37개 중 쓰기 능력이 있는 것)

| 분류 | 스크립트 |
|---|---|
| EXCHANGE_WRITE | `run_slippage_probe.py`(`--fire`), `run_signed_testnet_cycle.py`, `emergency_close.py`(`--confirm`) |
| EXCHANGE_READ (주문 키) | `diagnose_bracket_leg.py`(`/order/test`), `list_resting_orders.py`, `venue_contract.py`(`--run`) |
| APPROVAL/STATE 쓰기 | `promote_strategy_candidates.py`, `register_execution_stage.py`, `register_live_trading_budget.py`, `register_crypto_risk_limits.py`, `retire_strategies.py`, `disarm_live_strategies.py`, `import_crypto_history.py`(`--activate-pool`), `correct_live_outcome.py`, `clear_api_breaker.py`, `clear_bracket_breaker.py`, `ops/policy_bump_*.py` |
| 연구 저장소 쓰기 | `forward_cohort.py`, `hypothesis_trial.py`, `seed_forward_book.py`, `rescore_stale_holdout_candidates.py`, `dedupe_counterfactual_book.py` |
| 읽기·보고 | 나머지 |

### F-4. 안전 소견 (지시서 §9·§31 대조)

지시서 §31의 정지 조건을 하나씩 대조했다. **새로 발견된 정지 조건은 없다.** 기존에 알려진 것과 새로 적는 것은 다음과 같다.

**S-1 — kill/pause는 포지션 관리까지 멈춘다 (CURRENT, 기존 결정).**

- `scheduler.py:2538`: `execution_allowed`가 거짓이면 due 발화를 claim하고 **버린다**.
  - `crypto_pipeline`이 돌지 않으니 정산·보호 재설치·시간 청산·대조가 멈춘다.
  - `live_route.py:453` 주석: "A PAUSED or KILLED runtime never gets here … which is why the soft halt … exists."
- 긴급 청산은 `ACTIVE`+HARD에서만 돈다(`emergency_halt_problem`, 결정 49).
- **남는 보호:** 거래소에 걸린 브래킷 주문(손절 `closePosition`·익절 reduceOnly)은 그대로 있다.
- **복구 경로:** 인증된 운영자의 `/halt_trading hard`는 정지를 풀고 곧바로 ACTIVE+HARD로 옮긴다(`control.py`의 `halt_may_release_stop` 분기). 그러면 정산·보호·시간 청산·대조가 다시 돈다. 다른 문(비서)의 halt는 정지 아래 기록만 되고, 정지는 그대로다.
- **SOFT·HARD halt는 지시서 §3과 맞다.**
  - SOFT: 진입만 거부. `trading_allowed`가 거짓이 되고, 어댑터는 막지 않는다.
  - HARD: 어댑터가 reduceOnly·closePosition이 아닌 주문을 모두 거부한다(메인넷·테스트넷).
    보호·청산은 제어 상태를 읽지도 않는다(`control_refusal`, `test_mvp_runtime_crypto_hard_halt_egress.py`).
- 2026-09-15 조사가 P1으로 보고했고, Thomas가 "kill/pause 의미 유지 + Trading Soft Halt 신설"로 결정했다.
  이 리팩터는 이 의미를 바꾸지 않는다. 지시서의 halt 계약과 다르다는 점은 운영 문서에 계속 남겨 둔다.

**S-2 — 스케줄러 거래 발화가 주문 가능 어댑터를 만든다 (CURRENT, 새로 적음).**

- 무엇이 일어나나: `_refresh_venue_contract`(scheduler) → `venue_contract.py:1025` `select_order_adapter` →
  `MVP_LIVE_TRADING=real`이면 `BinanceFuturesOrderAdapter`가 만들어진다. `submit`·`cancel_order` 메서드를 가진 객체다.
- 실제로 부르는 것: `validate_order`·`position_mode`·`fetch_order`·`open_orders`·`algo_open_orders`뿐이다.
  시험이 모듈 단위로 이를 고정한다(PR4 결정 43: 라이브 키 자동 서명 GET·`/order/test` 허용).
- 문제: 명시 권한(주문 없음)보다 **능력**이 넓다. 이 모듈에 `adapter.submit` 한 줄이 들어오면 자율 관문 밖의 두 번째 송신
  경로가 된다. 그때 막는 것은 AST 핀 하나뿐이다.
- 권고: §M-2의 거래소 쓰기 명부 시험(PR-02)으로 먼저 덮는다. 어댑터를 읽기 전용 프로토콜로 좁히는 일(PR-15)은
  execution 층 변경이라 R5다.

**S-3 — 승격 문의 풀 설치가 사이클 writer를 덮어쓸 수 있다 (HISTORICAL — PR-S3로 고침, 아래는 고치기 전 기록).**

- **고친 내용 (PR-S3, 2026-09-30):** 승격 문은 풀을 읽을 때 그 파일의 다이제스트를 함께 받는다
  (`pool_state.load_active_pool_with_digest`). 설치(`install_active_pool(expected_digest=)`)는 다른 writer와 같은 잠금
  안에서 파일을 다시 해시하고, 읽은 것과 다르면 `STRATEGY_POOL_CHANGED`로 거부한다. 아무것도 쓰지 않으므로 사이에 낀
  해제·강등이 남는다. 운영자는 무엇이 바뀌었는지 본 뒤, 여전히 맞으면 같은 명령을 다시 실행한다(승인은 검증만 하고
  소비하지 않는다).
  `import_crypto_history --activate-pool`은 디스크의 풀을 바탕으로 만들지 않는 통째 교체라 다이제스트를 넘기지 않는다.

- 무엇이 일어나나: `promote_strategy_candidates.py:231`이 `load_active_pool`을 잠금 **밖**에서 읽는다.
  `:402` `install_active_pool`은 잠금을 쓰기 순간에만 잡고 파일 **전체**를 교체한다(`pool_state.py:214`).
- 겹치는 writer: 그 사이에 사이클의 `pool_transitions.apply_status_decisions`(강등)나 `live_tier.disarm_live_tier`(무장 해제)가
  쓰면, 그 변경은 사라진다.
- 영향:
  - 해제가 사라지면 LIVE tier가 되살아난다(넓히는 방향).
  - 다음 사이클의 허용치 판정이 다시 해제하므로 창은 한 사이클이다.
  - 지금은 단계 PAPER·LIVE 무장 0이라 실제 영향이 없다.
- 저장소 문서에서 추적 기록을 찾지 못했다. 권고: 설치 문에 "읽은 풀의 해시 = 잠금 안의 현재 해시" CAS를 넣는다.
  probe 계획 저장(`write_plan(expected_sha256=)`)과 같은 패턴이다. 승격 문 변경이라 R4, 별도 승인 과제다(PR-S3).

**그 밖의 대조 결과 (CURRENT).**

| 정지 조건 | 결과 |
|---|---|
| multiple uncontrolled exchange-write paths | 없음. 쓰기는 모두 env 게이트 + `control_refusal` 뒤 (F-2) |
| risk gate bypass | 없음. reduceOnly 아닌 주문은 봉인 스냅샷 없이 거부된다(`SubmitRefused`) |
| hot-path revalidation bypass | 자율 leg·probe 모두 재조회·봉인을 거친다. 테스트넷은 자기 가드를 쓴다(결정 22 "testnet 제외") |
| stage authorization bypass | 탈출 플래그 없음(PR1c) |
| research·analytics → execution | 없음 (E-1) |
| secret leakage | 키 env 이름을 읽는 곳은 `account`(읽기 키), `live_execution`(주문 키), `testnet_execution`·`run_signed_testnet_cycle`(테스트넷 키)뿐. 값은 확인하지 않았다(지시서 §23) |
| paper path가 실주문 가능 | 없음. `paper`는 어댑터를 import하지 않는다 (층 시험 + 관문 시험) |
| local state가 venue truth를 덮음 | 아니다. 대조가 RECONCILED가 아니면 진입 거부, 드리프트면 halt. 장부는 거래소가 없다고 하면 지운다(`live_reconcile`) |
| 보호 없는 라이브 포지션 생성 | 진입 뒤 보호 레그가 실패하면 즉시 청산을 시도한다(`ENTRY_NAKED_CLOSED`/`OPEN`). 청산이 실패하면 halt |

---

## G. 상태 소유 지도 (CURRENT)

경로는 `state/crypto/` 기준이다(`state.state_dir`). 메인넷이 아닌 venue는 `venues/<venue>/` 아래에 둔다.

| 상태 | 파일 | 권위 writer | 권위 reader | 기타 reader | 복구 동작 |
|---|---|---|---|---|---|
| 실행 단계 | `execution_stage.json` (+`.lock`) | `register_execution_stage.py`(승인 소비), DEMOTE 문 | `live_order`·`live_route`·`testnet_execution`의 진입 문 | `live_readiness`, `promotion` | 없음·손상 = READ_ONLY. 옛 파일 복원은 탐지 못 함(`EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md`, DRAFT) |
| 운영자 제어(모드·무장·halt) | `.runtime_governance_state/operator_control_state.json` | `control.ControlStore`(텔레그램·비서 문) | 스케줄러, `trading_allowed`, `control_refusal` | 보드 | 손상 = KILLED+HARD, 파일 삭제 = 원장에서 복원(UNARMED 기본) |
| 활성 풀·LIVE tier | `active_strategy_pool.json` | **writer 넷**: 승격 문(설치), `import_crypto_history --activate-pool`(설치), `pool_transitions`(상태), `live_tier.disarm_live_tier`(해제) | 사이클·라이브 경로 | 보드, 퍼널 | 손상 = 라우팅 안 함. S-3 경합 |
| 후보 | `strategy_candidates.jsonl` | `pool_state.append_candidates`(factory·proposer·트라이얼) | 승격 문 | 퍼널·forward | 자기 해시 검사 |
| 예산 | `live_trading_budget.json` | `register_live_trading_budget.py` | `live_order.resolve_live_order_limits` | 보드 | 없음·손상 = 한도 0(거부) |
| 리스크 한도 | `crypto_risk_limits.json` | `register_crypto_risk_limits.py` | `risk_limits.resolve_risk_limits` | 보드 | 없음 = 기본값. 손상 = 거부 |
| 라이브 장부 | `live_positions/<SYMBOL>.json` | `live_leg`(열기·닫기), `live_route`(정산) | 라이브 경로 | 보드, 긴급 청산 | 거래소가 권위. 드리프트면 halt |
| 일일 주문 수 | `live_order_counter.json` | `live_order.reserve_submission` | 최종 가드 | 보드 | 잠금 예약 |
| 브레이커(브래킷·API) | `live_bracket_failures.json`, `live_api_errors.json` | `live_order`, 해제는 운영자 스크립트 | 진입 문 | 보드, 감시 | 파일이 없으면 빈 기록(`live_order.py:949` `_empty_bracket_record`) = 삭제가 곧 리셋. 일일 카운터도 파일이 없으면 0(`:787`). 손상은 거부 |
| 진입 표식·쿨다운 | `live_entry_marks.json` | `live_order`(claim·release·stop cooldown) | 두 진입 문 | 보드 | 손상 = 진입 거부 |
| 사전 주문 스냅샷 | `pre_order_risk_snapshots.jsonl` | `pre_order_gate.verify_and_persist` | `submit_and_reconcile` | 보드 | 손상 줄 거부, 찢긴 꼬리 절단 |
| 라이브 결과 | `live_outcomes.jsonl` + `live_outcome_corrections.jsonl` | `live_pnl`(결산), `correct_live_outcome.py`(정정) | `breaker_watch`, `guards`, `live_allowance` | 보드 | 정정 레코드로 고친다 |
| 일일 손익 | 파일 없음 (파생) | — | `live_pnl.venue_daily_realized_net`(거래소 income), `daily_realized_pnl`(원장) | | 계좌 스냅샷에 거래소 수치가 없으면 브레이커가 걸린 것으로 본다(`live_route.py:583-591`, `venue_required`) |
| 연속 손실 | 파일 없음 (파생) | — | `guards._consecutive_losses`(풀), `live_allowance._consecutive_losses`(계보) | | §K |
| 스케줄러 heartbeat | `.runtime_governance_state/schedules.jsonl`, `heartbeat` | 코어 스케줄러 | 워치독 | Hermes | `RISK_LANE_WATCHDOG_V0.1.md`(DECIDED, 구현 중) |
| 시장 데이터 신선도 | 새로고침 표식(`*_refresh.json`) + 봉 시각 | market store들 | `feed_assembly.optional_data_health`, 진입 문 | 보드 | 나이는 봉 시작 기준 |
| 라이브 준비도 | 파일 없음 (파생) | — | `live_readiness`(11개 구성요소 3값 AND) | 콘솔 | |
| 계약 감시 | `venue_contract.json` + 표식·알림 | `venue_contract` | 진입 문 | 보드 | 6시간 유효, 위반은 즉시 FAIL |
| 승격 상태 | 풀 항목 `status`·`live_tier` | 위 풀 writer들 | | | |

**권위 writer가 여럿인 것은 풀 하나뿐이다.** 네 writer 모두 잠금을 쓰고, 상태 전이·해제는 "잠금 안 재읽기 후 자기 필드만"이다
(`pool_state.py` 머리 주석). 설치 문만 잠금 밖에서 읽는다(S-3).

---

## H. 부작용 지도 (CURRENT)

| 부작용 | 위치 |
|---|---|
| 거래소 쓰기 | §F-2의 W1~W5 |
| 거래소 서명 읽기 | `account`(읽기 키: 계좌·`/fapi/v1/income`), 주문 어댑터의 `fetch_order`·`open_orders`(주문 키), `venue_contract`, `live_readiness`(계좌) |
| 공개 네트워크 읽기 | `market_data` 수집기(Binance 공개, Coinalyze, Hyperliquid POST info), `live_filters`(exchangeInfo) |
| 파일 쓰기 | §G의 모든 파일, 레코드 원장(`store.LedgerStore`), 연구 저장소(`forward_*`, `counterfactual`, `candle_archive`, market store들) |
| env 읽기 | `select_env_gated` 호출 15곳(market_data 4, live_order 4, live_execution 2, live_position, live_pnl, paper, testnet, account), 확인 문구·수동 킬 env(`live_order`) |
| 시계 읽기 | 판정 시계 훅이 따로 있다(`live_route._entry_clock`·`_settle_clock`·`_notice_clock`, `pre_order_gate._send_clock`). 나머지는 `now` 인자 |
| 알림 | `live_route._notify_operator`, `breaker_watch`, `route_watch`, `venue_contract` 알림, API 브레이커 알림 |
| 증거로 쓰는 로그 | 레코드 원장(`crypto_cycle`, 거버넌스·감사), `live_governance` |
| 프로세스·스케줄 변경 | 없음 (crypto는 스케줄을 바꾸지 않는다. factory는 fork 자식) |

**순수 로직이 부작용과 섞인 곳.** 다음 추출 후보다.

| 위치 | 섞인 것 | 이미 순수한 것 |
|---|---|---|
| `live_route._run_gated_live_leg` (420줄) | 사실 수집(I/O)·정산·결정·게이트·송신 호출·기록 | `live_reconcile`, `order_request`, `pre_order_gate`의 순수 게이트 |
| `live_order` (2,041줄) | 순수 최종 가드 `evaluate_live_order_guard`와 파일 저장소 4종(카운터·두 브레이커·표식)과 한도 해석 | |
| `live_leg` (1,931줄) | 결과 분류(순수)와 어댑터 호출·장부 쓰기 | |
| `cycle.run_crypto_cycle` (~670줄) | 단계 호출과 기록 조립 | 지시서가 예시로 든 경계는 이미 `feed_assembly`·`cohort_retention`로 뺐다(PR7e-2·5·6) |
| `factory` (5,477줄) | 생성·백테스트·검증·기록 쓰기 | |
| `live_readiness` (2,106줄) | 구성요소 판정(순수)과 서명 계좌 읽기·렌더링 | |

---

## I. 도메인 모델 점검 (CURRENT)

| 지시서 개념 | 실제 표현 | 형태 | 경계를 넘는 곳 |
|---|---|---|---|
| MarketSnapshot | 수집기 반환 snapshot(캔들 행 목록 + 부착된 피드) | dict·list | market → cycle → paper |
| FeatureSnapshot | `features.latest_feature_row` 결과 행 | dict | cycle → paper·live_route |
| ResearchSignal | 없음. 후보 행(`strategy_candidates.jsonl`)이 연구 산출물 | dict (자기 해시) | strategy → decision |
| StrategySignal | `StrategySpec` 평가 결과, 페이퍼 route | dataclass + dict | paper → live_route |
| TradeProposal | `trade_plan.build_entry_plan` 결과 | dict | |
| PortfolioDecision | 없음 (라우팅 결과 + 표식의 전역 캡 재판정) | | |
| RiskDecision | `guards` 판정 dict, `pre_order_gate` 봉인 스냅샷(스키마 `pre_order_risk_snapshot.v0.1`) | dict + JSON 스키마 | risk → execution |
| OrderIntent | `live_entry` 결정의 `intent`, `order_request.build_order_request` 요청 | dict (`INTENT_BOUND_FIELDS` v2 봉인) | |
| ExecutionResult | `submit_and_reconcile` 결과(`reconcile_status`, `mismatches`, `exchange_order_id`) | dict | execution → live_route |
| ReconciliationResult | `live_reconcile.reconcile_positions` 결과(`status`, drift 코드) | dict | |
| TradeOutcome | 라이브 결과 행(`live_settlement` 빌더), 페이퍼 결과(`trade_plan.build_outcome_record`) | dict | outcome → risk (`live_ledger`) |
| FeedbackCycle | `feedback` 보고, `lifecycle` 결정 | dict | |

- 계약이 **스키마로 닫힌 것**: 단계 기록, 예산, 리스크 한도, 사전 주문 스냅샷, 계약 감시 기록. 모두 `schemas/`와 자기 해시가 있다.
- **dict가 층을 넘는 것**: 스냅샷·피처 행·route·intent·실행 결과·결과 행.
  - 필드 이름 표류는 봉인(`INTENT_BOUND_FIELDS`, `LINEAGE_FIELDS`)과 해시 버전으로 잡는다.
  - 옛 행 읽기 전용 집합(`PR2B_GATE_CHECK_IDS`, `legacy_read=True`)이 롤백 호환을 지킨다.
- **표류 위험:**
  1. `intent`와 bracket 기록이 따로 만들어진다(PR2b 함정: bracket은 결정의 `bracket` 기록으로 놓인다).
  2. 결과 행의 `r_basis` 라벨(`intent`·`intent_net_of_costs`·`filled`)을 읽는 쪽이 여럿이다.
  3. 후보 행의 `strategy_id`는 템플릿 간에 공유된다. 계통 키(`candidate_identity.outcome_attribution_key`)가 정본이다.
- **권고(PROPOSED): 새 범용 모델은 만들지 않는다.** 지시서 §26 원칙에 따른다. 이득이 확실한 것은 둘뿐이다.
  - 라이브 결과 행에 스키마 파일 추가(R2, 읽기만 검증).
  - 실행 결과 dict의 키 집합을 `order_request`의 상수로 고정(R1).
  둘 다 행동 불변이다.

---

## J. 계통(ID) 점검 (CURRENT)

**요약.** 전략 계통에서 결과까지는 레코드마다 필드를 복사해 이어진다. 데이터 스냅샷 id, 피처 스냅샷 id, 신호 id,
결정 id는 없다. `cycle_id`는 거래가 끝난 뒤에 붙는다. 라이브 결과는 수명주기로 돌아가지 않는다.

### J-1. 지시서 개념 → 실제 필드

| 개념 | 실제 필드 | 생성 위치·방식 | 이어지는 곳 | 상태 |
|---|---|---|---|---|
| data_snapshot_id | 사이클 `collection.output_sha256`, 연구 쪽 `evidence_input_sha256` | `market_data.py:874`, `factory.py:5118` | 사이클 레코드만 / 후보 행과 `candidate_id` 시드 | **없음** (id가 아닌 해시 두 개. 캔들 목록이 달라 같다고 볼 수 없다) |
| feature_snapshot_id | 없음. 사실상 `feature_row.timestamp`(봉 시작) | `features.py:1049` | `intent.candle_time`(`live_entry.py:816`) → 멱등 키, 봉 표식, 스냅샷 `lineage.candle_time` | **없음.** 포지션·결과에는 봉 시각도 없다 |
| research_signal_id | 없음 (route의 `primary_*` 필드) | `paper.py:596`, `trade_plan.py:266-273` | plan → intent | **없음** |
| profile_id | `candidate_id`(generation·rule hash·증거 해시 시드), `strategy_rule_hash`, `generation_id`, `strategy_artifact_sha256` | `candidate_identity.py:33`, `strategy.py:486-496`, `factory.py:4796`, `strategy_artifact.py:249` | 풀 항목 → route → plan → intent → 사전 주문 스냅샷 → 포지션 → 결과 | **완결** |
| approval_packet_id | `approval_id`(행동 지문 시드), `permission_decision_id` | `permission.py:674`, `:690` | LIVE 설치 때만 풀 항목 `live_tier_approval_id`, 스냅샷 `approved_profile.authority.approval_id` | **선택적** (OBSERVATION tier는 승인 참조가 없다) |
| approval_intake_id / decision_id | 없음. 저장소는 `approval_id`당 최신 레코드 | `approval_store.py:76-84`, 감사 참조 `audit:approval_decision:{approval_id}` | — | **없음** |
| 진입 결정 | `plan_live_entry` 결과에 id 없음 (`live_entry.py:1126`) | — | 사이클 레코드 `live_decision` | **없음** |
| risk_gate_id | 게이트 버전 상수 `pre_order_gate.v1`, 주문별 `pre_order_risk_snapshot_id` + 자기 해시 `risk_snapshot_sha256` | `pre_order_gate.py:59`, `:422-426` | 스냅샷 저장소, `bind_intent`가 셋을 intent에 싣는다. 포지션·결과에는 `risk_snapshot_sha256`만 | **완결** (단, 아래로 흐르는 것은 id가 아니라 해시) |
| order_intent_id | `idempotency_key` → `client_order_id` → `order_intent_id` | `order_identity.py:20-54` | intent, 스냅샷, P5 `order_fingerprint`. 포지션에는 `entry_client_order_id`만, 결과에는 없음 | **중복** (J-4) |
| execution_id | 없음. 거래소가 준 `exchange_order_id` | `live_execution.py:912-935` | 포지션 `entry_exchange_order_id` → 결과 `entry_order_id` (`live_leg.py:1452`) | **없음** (거래소 id가 대신함) |
| reconciliation_id | 없음. `reconcile_status`는 열거값 | `live_execution.py:914` | 사이클 레코드만 | **없음** |
| position | `position_id`(심볼·진입가·수량·시각), 미기장 `unbooked_position_id` | `live_position.py:233`, `:84-110` | 결과 `position_id` | **완결 / 중복** |
| outcome_id | `outcome_id`(position·closed_at·symbol), `settlement_id`(position·exit order) | `live_settlement.py:194-199` | `live_outcomes.jsonl`, 정정 `corrects_outcome_id` | **완결** |
| feedback_cycle_id | `performance_report_id` + `source_outcome_ids`, `strategy_lifecycle_decision_id` | `feedback.py:323-345`, `lifecycle.py:323,388` | 풀 항목 `lifecycle_decision_id`(`pool_transitions.py:149`). 보고서 자체는 저장되지 않음 | **라이브 없음, 페이퍼만** |
| cycle_id / pool_cycle_id | `short_id(symbol, timeframe, now)` / `short_id(contexts, now)` | `cycle.py:794`, `:1029` | 원장 키. `pool_cycle_id`는 어디에도 쓰이지 않는다 | J-3 |

### J-2. 빠진 고리

- **결과 → 사이클이 없다.** `build_live_position`은 `cycle_id` 인자를 받는다(`live_position.py:153,211`).
  하지만 자율 경로는 넘기지 않는다(`live_leg.py:931-950`). 넘기는 곳은 테스트넷(`testnet_execution.py:512`)뿐이다.
  결과 행에는 `cycle_id` 필드가 아예 없다.
- **결과 → intent·승인은 조인으로만 된다.** `outcome.risk_snapshot_sha256` → 스냅샷 행 → `lineage.*`·`approved_profile.authority.approval_id`.
- **`pool_cycle_id`는 저장되지 않는다.** 팬아웃을 다시 묶으려면 `created_at`을 맞춰 봐야 한다.
- **라이브 결과는 수명주기·C6 보고로 가지 않는다.** 이 문서가 직접 확인했다.
  - 수명주기와 C6 보고의 입력 `outcomes`는 `paper.read_outcomes`(`paper_outcomes.jsonl`)다. `cycle.py:294`, `:653`.
  - 라이브 결과는 손실 브레이커(`live_outcomes_for_analysis`, `cycle.py:332`)와 계보별 허용치로만 간다.
  - **HISTORICAL:** `live_settlement.py` LP5.4 주석(:92-95)은 라이브 결과가 "the lifecycle demoter, and the C6 feedback report"에
    읽힌다고 적는다. 필드는 그렇게 맞춰졌지만, 호출 경로는 연결되지 않았다.
  - 라이브의 계보별 통제는 `live_allowance`가 대신한다. 설계 의도였는지는 저장소에서 확인하지 못했다.
- **수명주기 결정은 어떤 결과를 썼는지 기록하지 않는다.** 시드는 표시 `strategy_id`+상태+시각이다.

### J-3. 너무 늦거나 이른 id

| id | 문제 |
|---|---|
| `cycle_id` | 사이클 끝(`cycle.py:794`)에 붙는다. 라이브 레그가 보내고 기장한 뒤다. 입력(symbol·timeframe·now)만으로 정해지므로 처음에 계산해 내려보낼 수 있다 |
| P5 `permission_decision_id` | 스냅샷 봉인(`live_route.py:761`) 뒤인 `:783`에서 생긴다. 스냅샷이 이 id를 담을 수 없다 |
| 라이브 `position_id` | 체결가·수량으로 체결 뒤에 만든다. 진입 intent의 `position_id`는 None이다 |
| `approval_id` | 요청 시점에 생긴다. 결정은 따로 id를 만들지 않는다 |
| `pre_order_risk_snapshot_id`, `outcome_id` | 시드에 `now`가 들어가 같은 대상을 다시 처리하면 새 id가 된다. 안정적인 것은 `settlement_id`다 |

### J-4. 중복 id

- `candidate_id` = 아티팩트의 `strategy_instance_id`(`strategy_artifact.py:189,215`).
- `generation_id`(후보·풀) = `strategy_generation_id`(plan·intent·포지션·결과). `lineage_of`가 둘 다 읽는다.
- 서로 다른 상수 `LINEAGE_FIELDS` 두 개: `candidate_identity.py:154`(3필드 튜플)와 `pre_order_gate.py:126`(목적별 dict).
  읽기 전용 레거시 `PRE_ARTIFACT_LINEAGE_FIELDS`도 있다.
- 주문 하나에 정체성 다섯: `idempotency_key`, `client_order_id`, `order_intent_id`, `intent_fingerprint`(v2), `order_fingerprint`.
  두 지문이 공유하는 것은 `idempotency_key`뿐이다.
- `strategy_id`는 세대마다 S001부터 다시 센다. 계통 키가 정본이다.

### J-5. 권고 (PROPOSED)

id 사슬은 바꾸지 않는다(지시서 §12). 봉인 필드(`INTENT_BOUND_FIELDS`)와 해시 버전에 묶여 있어 필드를 더하면 R4다.
나중에 할 수 있는 것은 두 가지다.

1. 사이클 시작 시 `cycle_id`를 계산해 라이브 포지션에 넘긴다. 필드는 이미 있고 테스트넷은 넘긴다.
   롤백 호환을 확인해야 하므로 R3다.
2. `live_settlement`의 HISTORICAL 문장을 고친다(R0).

수명주기에 라이브 결과를 넣는 것은 행동 변경이라 이 리팩터 범위 밖이다.

---

## K. 중복 분석 (CURRENT)

PR7과 2026-08 §G4(`docs/REMAINING_WORK.md`)가 이미 많이 정리했다.

- `outcome_math.net_result_r`는 비용 변환을 `cost.outcome_net_r`에 위임한다(주석 "One concept, one owner").
- 리스크 한도 좁히기는 `stricter_limits` 하나다.
- 주문 id 도우미는 `order_identity` 하나다.

남은 중복은 다음과 같다.

| 개념 | 정본 | 중복 | 동작 차이 | 통합 위험 | 권장 소유 |
|---|---|---|---|---|---|
| 연속 손실 | `guards._consecutive_losses` | `live_allowance._consecutive_losses` | **있다.** guards는 `is_probe` 행을 양방향으로 건너뛰고 순 R(`pnl_r`=`_judged_r`)을 읽는다. live_allowance는 날 `result_R`을 읽고 probe를 건너뛰지 않는다. docstring은 "Deliberately the same shape"라고 한다 | R4 (허용치 판정이 바뀔 수 있다) | `outcome_math`의 순수 함수 하나 + 모집단 인자. 통합 전에 두 모집단에서 결과가 같은지 실측 |
| 일일 손익 | `live_pnl.venue_daily_realized_net`(거래소 income) | `live_pnl.daily_realized_pnl`(원장 합), `guards._pnl_since`(R 합) | 단위가 다르다(USDT 거래소·USDT 원장·R). 의도된 이중 출처 | 통합 금지 (서로 다른 질문) | 현 위치 유지, 문서화 |
| 타임프레임 길이 | `market_data.TIMEFRAMES`(분) | `market_data.POSITIONING_PERIOD_SECONDS`(초, 벤더 주기), `strategy.ALLOWED_TIMEFRAMES`, `dashboard._TIMEFRAME_ORDER` | 값은 일치. 뒤의 둘은 집합·순서 | R1 | `TIMEFRAMES`에서 파생 |
| 심볼 정규화 | `live_order.normalize_symbols` | `live_position.position_symbol`(행에서 추출) | 다른 일 | 없음 | 유지 |
| 수수료·슬리피지 | `cost.DEFAULT_*_BPS` | `live_entry.MAX_ENTRY_SLIPPAGE_BPS = DEFAULT_SLIPPAGE_BPS`(같은 객체) | 없음 | 없음 | 유지 (tunables가 소유자 추적) |
| 나이 한도 | `pre_order_gate.MAX_ACCOUNT_AGE_SECONDS`(60) | `MAX_SNAPSHOT_AGE_SECONDS`, `live_entry.MAX_ORDER_BOOK_AGE_SECONDS`(같은 값의 별칭), `account_store.STALE_AFTER_SECONDS`(2h), `live_readiness.RECORDED_GATE_STALE_AFTER_SECONDS`(2h) | 뒤의 둘은 다른 질문(보드) | 없음 | 유지 |
| 노출 계산 | `live_position.compute_open_notional_usdt`(거래소 스냅샷) | `local_open_notional_usdt`(장부), 표식의 금액(`live_order`) | 출처가 다르고 캡은 높은 쪽을 쓴다 | R4 | 유지. portfolio 모듈을 만들 때 읽기 함수만 모은다 |
| 단계·승인 검사 | `execution_stage.required_stage` + `resolve_execution_stage` | 진입 문 3곳이 각자 호출 | 없음 (한 소유자) | 없음 | 유지 |
| kill 검사 | `ControlState.trading_allowed`(진입), `execution_allowed`(스케줄러), `control_refusal`(어댑터) | | 층마다 질문이 다르다 (의도) | 통합 금지 | 유지 |


### K-1. 연속 손실 두 계산 실측 (2026-09-30, PR-06, 읽기 전용)

**방법.** 운영 라이브 결과 원장(`live_outcomes.jsonl` 28행 + 정정 1건)을 복사해, 저장소의 두 함수
(`guards._consecutive_losses`, `live_allowance._consecutive_losses`)를 같은 행에 적용했다. 검증 읽기(`read_live_outcomes`) →
`live_outcomes_for_analysis`를 거친 행을 쓰고, 청산이 하나 늘 때마다 두 값을 비교했다. 운영 상태에는 쓰지 않았다.

**표본.** 읽을 수 있는 청산 26행(2026-08-06~08-21), 제외 2행(R을 정직하게 매길 수 없는 행). 26행 중 probe가 20행,
전략 행이 6행(계보 4개)이다. 표본이 작다.

| 모집단 | 청산 시점 수 | 두 규칙이 다른 시점 | 비고 |
|---|---:|---:|---|
| 풀 전체 (풀 브레이커가 읽는 것) | 26 | 15 | 전부 probe 행에서 갈린다. 최대 차이 3 |
| 전략 계보별 (허용치가 읽는 것, 계보 4개) | 6 | **0** | 허용치 판정(2연패 이상)도 같다 |
| probe 키별 (`sid:PROBE-*`, 3개) | 20 | 11 | 허용치 판정이 달라지는 시점 4. 단, 무장된 계보가 아니라 허용치는 이 행들을 읽지 않는다 |

**읽는 법.**

- **각 규칙이 실제로 읽는 모집단에서는 결과가 같다.** 허용치는 무장된 계보의 행만 읽고, probe 행은
  `sid:PROBE-…` 키로 귀속되므로 그 모집단에 들어오지 않는다. 풀 브레이커는 probe를 건너뛴다.
- **차이는 규칙을 바꿔 끼울 때만 난다.** 허용치의 규칙을 풀 전체에 쓰면 probe 손실을 연패로 세고(최대 +3),
  probe의 우연한 이익이 실제 연패를 지운다(`guards`는 2인데 0으로 읽는 시점 5개). `guards` docstring이 적은
  2026-08-17 사고와 같은 모양이다.
- **순 R과 적힌 R의 차이는 라이브 행에서 0이다.** 26행 모두 `outcome_math.net_result_r`가 값을 내지 못해
  `guards._judged_r`가 저장된 `result_R`로 되돌아간다. §K 표의 "순 R 대 날 R" 차이는 페이퍼 행에서만 실현된다.

**통합에 주는 뜻 (PROPOSED).**

- 한 함수(`skip_probes`와 R 읽는 법을 인자로)로 합쳐도 현재 데이터에서 두 호출자의 결과가 모두 재현된다.
  인자가 지금 동작을 그대로 옮기면 행동 불변이라 R1이다.
- 허용치를 `guards`의 규칙으로 **바꾸는** 것은 오늘 데이터에서는 같은 결과지만 행동 변경으로 다뤄야 한다.
  라이브 행에 순 R을 매길 수 있게 되면 두 규칙이 갈리고, 그때 허용치가 더 엄해진다.
- 전략 행이 6개뿐이라 "같다"는 관찰은 약하다. 현재 차이는 PR-05의 시험 두 개가 입력 수준에서 고정한다
  (`tests/test_mvp_runtime_crypto_characterization.py`).

---

## L. 목표 구조 (PROPOSED)

### L-1. 폴더 트리는 채택하지 않는다

지시서 §15의 `crypto/market/…/operations/` 트리는 **논리적으로는 이미 있다.** 층 지도가 그 트리이고, 시험이 방향을 강제한다.
물리적으로 옮기는 비용은 PR7 조사 §6이 쟀다.

- 시험 파일 162개가 crypto 모듈 경로를 import한다.
- `monkeypatch.setattr`가 약 1,000곳이다(시험 전체).
- 스크립트 37개, tunables 색인, 진단 색인, 도메인 격리 문 목록, 문서 참조 수백 곳이 경로에 묶여 있다.

옮기는 PR은 새로 막는 것이 없다. 반면 패치가 조용히 빗나가는 위험이 있다(`scripts/ops/patch_reach.py`가 PR7e에서 잰 문제). 이 판단은
PR7 조사 §8 질문 1의 권고와 같다. **Thomas 결정은 아직 기록되지 않았다**(D-1).

### L-2. 대신 할 것

1. **층 지도에 없는 경계를 시험으로 만든다.** 거래소 쓰기 명부, 분석 층 egress 금지, 스크립트 능력 명부(§M).
2. **큰 모듈을 같은 층 안에서 나눈다.** 순수 부분을 떼고, 원래 모듈이 같은 객체를 재수출한다. PR7e와 같은 방식이다.
3. **이름 부채 하나.** `live_promotion` → 카나리 기록 읽기라는 것이 드러나는 이름. 재수출 별칭은 유지한다.

### L-3. portfolio 층

만들지 않는다(PROPOSED). 지금 portfolio 역할을 하는 코드는 모두 진입 문 안의 거부 검사다(전역 캡 재판정, 슬롯 캡).
떼어 내면 R4 변경이 된다. 계획된 "Portfolio Correlation Exposure"(§Q)가 들어올 때 **읽기 전용 보고**로 먼저 두고,
문으로 올릴지는 별도 결정으로 한다. `PORTFOLIO_INDEPENDENCE_V0.1.md` Q3(상관 게이트)가 이미 "조건이 차면 재질문"으로 남아 있다.

### L-4. 분할 대상 (층 유지)

| 모듈 | 나눌 것 | 층 | 위험 |
|---|---|---|---|
| `factory` | 생성·백테스트·홀드아웃·기록 쓰기를 섹션 단위로 | strategy | R2 (연구, 거래 경로 밖) |
| `live_readiness` | 구성요소 판정(순수) / 렌더링 / 서명 읽기 | report | R2 |
| `cycle.run_crypto_cycle` | 단계별 이름 붙은 함수 (AST 동일성은 포기) | orchestration | R3 |
| `live_order` | 파일 저장소 4종 / 한도 해석 / 최종 가드(순수) | execution | R4 (최종 가드) |
| `live_leg`·`live_route` | 결과 분류(순수) / 송신 호출 | execution·orchestration | R5 |
| `live_execution` | 메인넷 어댑터 / 테스트넷과 공유할 서명 요청 | execution | R5 |

---

## M. 의존 규칙

### M-1. 현재 규칙 (CURRENT)

§E-1 표 전부.

### M-2. 추가할 규칙 (PROPOSED — 시험만, 코드 불변)

| # | 규칙 | 시험 방식 | 지금 위반 |
|---|---|---|---|
| M-2a | 거래소 쓰기 메서드(`.submit`, `.cancel_order`)와 `submit_and_reconcile`, `select_order_adapter`, `select_testnet_order_adapter`, `select_live_gate`를 **부르는** 곳은 명부의 (파일, 함수, 목적) 쌍뿐이다. `runtime/`과 `scripts/` 전체를 AST로 스캔한다 | 파일명이 아니라 호출 이름과 수신자로 판정. 명부 항목은 "줄기만 한다"(층 시험과 같은 규칙) | 없음 (현재 호출자를 그대로 등록) |
| M-2b | outcome·report 층은 `live_execution`, `live_leg`, `testnet_execution`, `live_route`를 import하지 않는다 | 층 시험에 "금지 쌍" 추가 | **있다** (§E-2의 5개 간선). 이름으로 고정된 예외로 시작해 PR-04에서 0으로 줄인다 |
| M-2c | crypto를 import하는 스크립트는 능력 분류(§F-3) 명부에 있어야 한다. EXCHANGE_WRITE는 주문 수 시험(`reserving`)과 같은 목록이어야 한다 | 명부 대조 | 없음 |
| M-2d | 주문·테스트넷 키 env 이름(`ORDER_API_*_ENV`, `TESTNET_API_*_ENV`)을 참조하는 모듈은 명부 3곳뿐이다 | AST로 이름 참조 스캔 | 없음 |
| M-2e | strategy·decision 층(연구)은 `select_*` env 게이트 중 거래 관련(`LIVE_TRADING_ENV`, `TESTNET_TRADING_ENV`)을 쓰지 않는다 | 이름 참조 스캔 | 없음 |

M-2b의 금지 집합은 **송신하는 모듈**이다. 관문 시험의 `LIVE_ORDER_MODULES = {live_execution, live_position, live_leg}`와는 목적이 다르다. `live_position`(장부)은 보드·긴급 청산이 읽어야 하므로 금지하지 않는다. 대신 `live_route`와 `testnet_execution`을 넣는다.

M-2a는 지시서 §22의 "only approved execution modules may import exchange-write client"를 **import가 아니라 호출**로
판정한다. import로 판정하면 `venue_contract`·`list_resting_orders`처럼 어댑터를 읽기용으로 쓰는 곳이 걸리거나 빠진다.

---

## N. 이행 계획 (PROPOSED)

원칙: 지시서 §18의 순서(특성화 → 계약 → 새 모듈 → 동등성 → 호출자 한 곳 → 회귀 → 나머지 → 옛 경로 제거)를 따른다.
도구는 PR7에서 만든 것을 쓴다.

- `scripts/ops/crypto_record_capture.py`: 두 커밋의 시험 기록을 값 단위로 비교한다.
- `scripts/ops/crypto_request_log.py`: 순수 이음매가 만든 주문 요청을 기록한다.
- `scripts/ops/patch_reach.py`: 옮긴 함수에 패치가 여전히 닿는지 잰다.

| 단계 | 내용 | 위험 | 선행 조건 |
|---|---|---|---|
| N0 | 이 문서 | R0 | — |
| N1 | 경계 시험 3개 (M-2a·c·d, M-2b는 이름 예외로 시작) | R1 (시험만) | N0 승인 |
| N2 | 특성화 시험 보강 (§O의 빈칸) | R1 | N1 |
| N3 | 분석·보고 층 상수 이동 (§E-2 간선 0으로) | R2 | N1 |
| N4 | 순수 로직 분할: `factory` 섹션, `live_readiness` 판정부 | R2 | N2 |
| N5 | `cycle.run_crypto_cycle` 단계 추출 | R3 | N2 + 기록 비교 |
| N6 | 중복 통합 준비: 연속 손실 두 모집단 실측 (보고만) | R0 | — |
| N7 | `live_order` 저장소 분할 (최종 가드 불변) | R4 | N2, 독립 리뷰 |
| N8 | S-3 CAS (행동 변경, 별도 승인) | R4 | Thomas 승인 |
| N9 | 실행 층 분할 (`live_leg`·`live_route` 순수부, 어댑터 읽기 프로토콜) | R5 | N1~N7 배포·관측 |
| N10 | 옛 재수출 제거 | R2 | 호출자 0 확인 |

N7 이상은 앞 단계가 배포되고 독립 리뷰를 받은 뒤에만 한다(지시서 §17).

---

## O. 특성화 시험 계획

지시서 §20 목록별로 **이미 있는 시험**과 빈칸을 적는다. 이름은 CURRENT 시험 파일이다.

| 대상 | 있는 시험 | 빈칸 (PROPOSED) |
|---|---|---|
| PreOrderRiskGate | `test_mvp_runtime_crypto_pre_order_gate.py` (봉인·검증·저장·손상) | 없음 |
| hot-path revalidation | `test_mvp_runtime_crypto_live_entry.py`(`test_each_re_read_fact_only_narrows` 등) | 없음 |
| execution stage | `test_mvp_runtime_crypto_execution_stage.py`, `test_register_execution_stage.py`, 단계 인자 필수 핀 | "READ_ONLY·SHADOW·PAPER는 페이퍼 동작이 같다"를 명시하는 시험 (현재 의미의 고정) |
| live readiness | `test_mvp_runtime_crypto_live_readiness.py` | 없음 |
| live routing | `test_mvp_runtime_crypto_live_route.py`(패치 131곳) | `run_crypto_cycle` 단계 순서 핀(정산 → 허용치 → 라이브 → 수명주기)을 기록 비교로 |
| position sizing | `test_mvp_runtime_crypto_live_sizing.py`, `test_mvp_runtime_crypto_vol_sizing.py` | 없음 |
| protective orders | `test_mvp_runtime_crypto_live_leg.py`, bracket diagnosis | 없음 |
| kill switch / halt | `test_mvp_runtime_crypto_hard_halt_egress.py`, 스케줄러 skip(`test_mvp_runtime_scheduler.py:564`, kill·pause → `skipped_not_active`) | **S-1 고정 시험**: 그 skip이 라이브 정산·보호까지 멈춘다는 결과를 명시적으로 고정한다. 바뀌면 결정이 필요하다는 신호가 된다 |
| venue reconciliation | `test_mvp_runtime_crypto_live_position.py`, `test_mvp_runtime_crypto_live_route.py`(전용 `live_reconcile` 시험 파일은 없다) | `live_reconcile` 순수 함수 단위 시험 |
| time-based exits | `live_route` 시험 | 없음 |
| paper execution | `test_mvp_runtime_crypto_paper.py` | 없음 |
| signed testnet | `test_mvp_runtime_crypto_testnet_path.py` | 없음 |
| live execution | `test_mvp_runtime_crypto_live_execution.py` | 거래소 쓰기 명부(M-2a) |
| 풀 writer | `pool_transitions`·`live_tier` 시험 | **S-3 현재 동작 고정**: 설치 문이 사이에 끼인 해제를 덮어쓴다는 사실을 xfail이 아닌 "현재 동작" 시험으로 적고 N8에서 뒤집는다 |
| 연속 손실 | `guards`·`live_allowance` 시험 | 두 함수에 같은 입력(probe 행 포함)을 넣은 차이 고정 시험 |

---

## P. 회귀 불변식

지시서 §21 목록과 이를 지키는 시험이다. 빈칸이 있는 것은 PR-02·03이 채운다.

| 불변식 | 지키는 것 (CURRENT) | 보강 |
|---|---|---|
| 단계 상향 건너뛰기 없음 | `execution_stage` 전이 시험 (`STAGE_SKIP_REFUSED`) | — |
| 연구 → 라이브 자동 승격 없음 | 층 시험, `lifecycle` "never auto-promote", 승격은 승인 소비 | — |
| 전략 → 거래소 직접 실행 없음 | 층 시험 | M-2a |
| 두 번째 라이브 관문 없음 | 관문 시험 2개 | M-2a (스크립트 포함) |
| 리스크 허가 없는 라이브 진입 없음 | `submit_and_reconcile`(`GUARD_NOT_APPROVED`, 봉인 스냅샷) | — |
| 재검증 없는 라이브 진입 없음 | `live_entry` 재조회 시험 | — |
| 노후·결측 증거로 진입 없음 | 신선도·선택 데이터 시험(PR2c·PR2d) | — |
| halt 중 노출 증가 없음 | `hard_halt_egress` 시험 | — |
| halt 중 reduce-only 청산 가능 | `test_exits_and_protection_go_out_under_a_hard_halt`, `test_an_exit_never_reads_the_control_state` | S-1 고정 시험 (kill/pause는 예외임을 명시) |
| venue state가 권위 | `live_position`·`live_reconcile` 시험 | — |
| 보호 주문 실패는 fail-closed | `live_leg` 시험(naked close) | — |
| 실행 불확실성이면 팬아웃 정지 | `test_mvp_runtime_crypto_cycle.py`의 `live_halt` 단언 | — |
| 선택 증거 결측이 양의 증거가 되지 않음 | PR2d-2 시험, readiness 3값 | — |
| paper가 실 쓰기 엔드포인트에 못 닿음 | 층 시험 | M-2a |
| 연구·분석이 쓰기 엔드포인트에 못 닿음 | 층 시험 (연구) | M-2b (분석은 지금 import 간선이 있다) |

---

## Q. 계획된 개선의 자리 (PROPOSED)

**전제:** `crypto/`의 새 연구 장치는 첫 코호트 판정까지 멈춰 있다(Thomas 2026-09-26, 검토 D3, `CLAUDE.md`).
그래서 아래 대부분은 지금 착수 대상이 아니다.

| 개선 | 들어갈 층·모듈 | 현재 상태 | 메모 |
|---|---|---|---|
| Forward Cohort Validation | decision `forward_cohort` | 구현됨 (`FORWARD_COHORT_OFF_POOL_V0.1.md`) | 새로 둘 곳 없음 |
| Live Slippage Distribution | outcome (`live_settlement`의 `realized_stop_slippage_bps` 옆), 스크립트 `measure_live_slippage.py` | D3 예외(검토 C2) | 읽기 전용 |
| Portfolio Correlation Exposure | strategy `independence`(보고) → 문으로 올리면 risk | Q3 재질문 대기 | L-3 |
| Strategy × Regime Evaluation | strategy (`robustness` 옆) | D3로 정지 | `FORWARD_VERDICT_REGIME_EPISODES_V0.1.md`(RECORD) |
| Hypothesis-Driven Strategy Factory | strategy `proposer`·`forward_trial` | 구현됨 (`HYPOTHESIS_TRIAL_V0.1.md`) | |
| Stress Cost Backtesting | strategy `cost`·`factory` | D3로 정지 | `factory` 분할(N4) 뒤가 쉽다 |
| Paper/Testnet/Live Gap KPI | report (`live_readiness` 판정부 분리 뒤 새 보고 모듈) | 없음 | 읽기 전용 |
| Reliability-Weighted Position Sizing | risk `live_sizing` | 없음 | R4, 별도 승인 |
| Operator Dashboard Simplification | report `dashboard`·`live_readiness` | 없음 | N4 뒤 |
| Multi-layer Heartbeats | 코어 `fire_watchdog`·`heartbeat` | `RISK_LANE_WATCHDOG_V0.1.md` DECIDED, 구현 중 | crypto 밖 |

---

## R. 위험 등록부

| 단계 | 위험 | 재무 영향 | 실패 모드 | 롤백 | 필요한 시험 |
|---|---|---|---|---|---|
| N1 경계 시험 | 명부가 현재 호출자를 빠뜨려 CI가 붉어짐 | 없음 | 시험 실패 | 시험 PR 되돌리기 | 메타 시험(합성 트리에서 위반 검출) |
| N2 특성화 | S-1·S-3 고정 시험이 "결함 승인"으로 읽힘 | 없음 | 오독 | — | docstring에 결정 출처 |
| N3 상수 이동 | 옛 모듈 패치가 빗나감 | 없음~낮음 | 시험이 아무것도 검사 안 함 | 되돌리기 | `patch_reach`, 재수출 동일성 핀 |
| N4 순수 분할 | 연구 결과 변화 | 낮음 (거래 경로 밖) | 후보·판정 해시 변화 | candidate 태그 롤백 | 기록 비교, 운영 저장소 :ro 대조 |
| N5 사이클 단계 추출 | 단계 순서 변경 | 높음 | 정산 전 진입 등 | candidate 롤백 | 단계 순서 핀, 기록 비교, 첫 파이어 관측 |
| N7 `live_order` 분할 | 최종 가드·카운터 의미 변화 | CRITICAL | 일일 캡 우회 | candidate 롤백 | 주문 경로 해시, 변이 시험, 독립 리뷰 |
| N8 S-3 CAS | 승격이 경합에서 실패 | 낮음 | 승격 재시도 필요 | 되돌리기 | 경합 강제 교차 시험 |
| N9 실행 분할 | 송신·보호 동작 변화 | CRITICAL | 무보호 포지션, 이중 송신 | candidate 롤백, 긴급 청산 | 요청 로그 바이트 동일, PR7e-4 때와 같은 어댑터·브래킷 시나리오 비교, 변이, 독립 리뷰, 테스트넷 사이클 1회 |
| N10 재수출 제거 | 외부 스크립트 import 실패 | 낮음 | 운영자 문 ImportError | 되돌리기 | 스크립트 `--help` import 확인 |

배포 규칙은 기존과 같다. 한 후보씩, `deploy_preflight.py`, 롤백 태그, 발화 창 회피, 첫 파이어 관측. 런타임을 바꾸지 않는
시험·도구 PR은 배포하지 않는다.

---

## S. 권장 PR 순서

| PR | 범위 | 파일 | 행동 변경 | 런타임 영향 | 시험 | 롤백 |
|---|---|---|---|---|---|---|
| PR-01 | 이 문서 + STATUS 재생성 | `docs/proposals/…` | 없음 | 없음 | `test_design_record_lifecycle.py` | 되돌리기 |
| PR-02 | 거래소 쓰기 명부 시험(M-2a) + 키 env 명부(M-2d) | `tests/test_mvp_runtime_crypto_egress_roster.py`(신규) | 없음 | 없음 | 메타 시험 포함 | 되돌리기 |
| PR-03 | 스크립트 능력 명부(M-2c) + 분석 층 금지 쌍(M-2b, 5개 이름 예외) | 층 시험, 신규 시험 | 없음 | 없음 | 메타 시험 | 되돌리기 |
| PR-04 | §E-2 간선 제거: 상수를 `vocabulary`로, `live_promotion`은 `order_request`에서. HISTORICAL 주석 정정(`live_execution`·`live_route`·`live_settlement` 머리 주석, §B-3·§J-2) | `vocabulary`, `live_readiness`, `route_watch`, `live_promotion`, `live_route`(재수출), 주석 3곳 | 없음 | 없음 (같은 객체) | 재수출 동일성, 기록 비교, 예외 0 핀 | candidate 롤백 |
| PR-05 | 특성화 시험: S-1 의미, S-3 현재 동작, 연속 손실 차이, 사이클 단계 순서, 단계별 페이퍼 동일 | 시험만 | 없음 | 없음 | — | 되돌리기 |
| PR-06 | 연속 손실 두 모집단 실측 보고 | 스크래치·문서 | 없음 | 없음 | — | — |
| PR-07 | `live_readiness` 판정부 분리 | `live_readiness`(+신규 report 모듈) | 없음 | 없음 | 기록 비교, 보드 텍스트 바이트 동일 | candidate 롤백 |
| PR-08~10 | `factory` 섹션 분할 (생성 / 백테스트·검증 / 기록) | `factory`(+신규 strategy 모듈) | 없음 | 연구 경로 | 기록 비교, 후보 해시 동일, 운영 :ro 대조 | candidate 롤백 |
| PR-11 | `cycle.run_crypto_cycle` 단계 함수 추출 | `cycle` | 없음 | 사이클 경로 | 단계 순서 핀, 기록 비교, 패치 도달 | candidate 롤백 |
| PR-12 | `live_order` 저장소 4종 분리 (최종 가드 불변) | `live_order`(+신규 execution 모듈) | 없음 | 주문 경로 | 주문 경로 해시, 변이, 독립 리뷰 | candidate 롤백 |
| PR-S3 | 승격 문 CAS (별도 승인 과제) | `pool_state`, `promote_strategy_candidates` | **있다** (경합 시 설치 거부) | 운영자 문 | 경합 시험 | 되돌리기 |
| PR-13~15 | 실행 층: `live_leg` 결과 분류 순수화, `live_route` 사실 수집 분리, 어댑터 읽기 프로토콜(S-2) | execution·orchestration | 없음 | 송신 경로 | 요청 로그·어댑터·브래킷 시나리오 동일, 테스트넷 사이클 | candidate 롤백 |
| PR-16 | 옛 재수출 제거 | 여러 곳 | 없음 | 없음 | 호출자 0, 스크립트 import | 되돌리기 |

**PR-08~10의 순서 (실측 2026-09-30).** `factory` 안에서 섹션끼리 무엇을 읽는지 재 보니, 리플레이 백테스트만 파일의
다른 섹션을 읽지 않고 템플릿 공간이 리플레이의 `holdout_split_index`를 읽는다. 그래서 순서는 리플레이(PR-08, `backtest`
모듈) → 템플릿 공간(PR-09, `template_space` 모듈) → 생성기(PR-10, `generator` 모듈)다. 기록 쓰기(`run_factory`)와 회전 커서·절제·퓨전·트라이얼은 `factory`에 남는다.

**하지 않는 것:** 폴더 이동, portfolio 층 신설, 단계·승인·halt 의미 변경, 임계값·크기·전략 파라미터 변경. 이 가운데 하나라도
필요해지면 별도 제안서로 다룬다.

---

## T. 최종 권고

**지금 리팩터해야 하나?** 부분적으로만이다. 구조 분해(PR7)는 끝났다. 지금 가치가 큰 것은 코드 이동이 아니라
**경계 시험 추가**다. 코드를 바꾸지 않고 명시 권한을 시험으로 만든다.

**무엇부터?** PR-02(거래소 쓰기 명부)와 PR-03(스크립트·분석 층 명부)이다. 둘 다 시험만이다. 그다음 PR-04(분석 층 상수
이동, 행동 불변)와 PR-05(S-1·S-3·연속 손실의 현재 동작 고정)를 한다.

**아직 건드리지 말 것.**

- `live_execution`·`live_leg`·`live_route`·`live_order`·`live_entry`·`pre_order_gate`·`execution_stage`·`control` 코드.
- 풀 writer들.
- 연구 모듈의 판정 규칙. D3 정지 중이고, 판정 규칙은 에포크 경계에서만 완화한다.

**바뀌지 않아야 할 것.** 단계 사다리와 목적별 요구 단계, `select_env_gated` 단일 스위치, `control_refusal`의 보호·청산 무조건
통과, 봉인 스냅샷 없는 비-reduceOnly 송신 거부, 진입 경로 단일성, kill/pause·SOFT·HARD의 현재 의미(S-1 포함),
거래소 우선 대조, 층 순서.

**가장 안전한 순서.** N0 → N1 → N2 → N3 → N4 → N5 → N7 → N9 → N10이다. 행동 변경(N8)은 따로 승인받는다.
한 PR에 한 가지만 넣고, 런타임을 바꾸는 PR은 한 후보씩 배포·관측한다.

**Thomas 결정이 필요한 것.**

- **D-1.** 하위 패키지 이동을 하지 않는다(L-1 권고)는 것을 확정할지.
- **D-2.** PR-02·03(시험만) 착수.
- **D-3.** S-3(승격 문 CAS)을 행동 변경 과제로 받을지.
- **D-4.** S-1을 지시서의 halt 계약과 다른 "기존 결정"으로 유지할지. 다시 열려면 `CONTROL_LANE_SEPARATION_V0.1.md` K4·K5와 같이 본다.
- **D-5.** S-2를 PR-02의 명부로 충분하다고 볼지, 어댑터 읽기 프로토콜(PR-15, R5)까지 갈지.
