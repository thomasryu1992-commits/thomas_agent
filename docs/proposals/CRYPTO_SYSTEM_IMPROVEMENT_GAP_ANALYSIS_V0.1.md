# 제안: 크립토 시스템 개선안 — main 대비 gap 분석 (v0.1)

**상태:** IMPLEMENTED 2026-09-30 — Q1·Q2·Q3 권고대로(Thomas) 결정·구현: forward cohort 읽기 열은 D3 밖(PR-D #1067), 슬리피지 재가격 열은 C2 예외(PR-E #1069), pause/kill 의미는 유지(변경 없음). PR-A·B·C는 #1050·#1051·#1052. 외부 개선안 16개 절 중 이미 구현 5, 부분 구현 5, 결정에 따라 보류 6이고, 권한을 새로 만드는 항목은 없다. 보류 6건은 각 항목의 조건(첫 cohort 판정, 슬리피지 실측, 에포크 경계)이 차면 이 문서를 근거로 다시 연다.

**대조 기준:** origin/main `70328a10`(#1039), 2026-09-30. 호스트 단계는 읽기 전용으로 확인했다
(`register_execution_stage --show`): **PAPER**, 정책 1.6.1.
**입력:** Thomas가 준 "Thomas Agent Crypto System — Improvement Plan"(이하 "개선안"). 절 번호 §3–§17은 개선안의
번호다.
**한계:** 거래소·키·라이브 돈 경로는 코드를 읽기만 했다. 운영 수치는 문서에 기록된 값을 날짜와 함께 인용했다.

검증 표기는 시스템 점검(`SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`)을 따른다. **[확인]**은 코드나 명령으로 직접 확인한
것, **[문서]**는 저장소 문서에 기록된 값, **[추정]**은 추론이다.

---

## 0. 한 줄

**개선안의 방향은 이 저장소가 이미 내린 결정과 거의 같다. 그래서 대부분이 "지을 것"이 아니라 "기다릴 것"으로 분류된다.**

- 개선안의 P0 세 가지(forward 검증, 슬리피지 분포, 포트폴리오 상관)는 모두 기계장치가 이미 있다. 막고 있는 것은 **데이터**다.
  - 단계가 PAPER라서 라이브 체결은 쌓이지 않는다.
  - forward 판정에는 달력 시간이 필요하다.
  - 시스템 점검 C0의 결론도 같다: 병목은 거래 빈도 × 달력 시간이다.
- P1·P2의 연구·표시 항목(regime 평가, 가설 factory, stress cost, gap KPI, 대시보드)은 두 결정에 걸린다.
  - **D3**(Thomas 2026-09-26): 첫 forward-cohort 판정까지 `crypto/`에 새 가설·트라이얼·표시 장치를 만들지 않는다.
  - **RESEARCH_EPOCH Q3=B**: 판정 규칙은 에포크 경계에서만 완화한다.
- **개선안의 원칙과 코드가 실제로 어긋나는 곳이 하나 있다(§13).** `/pause`·`/kill`은 스케줄러 전체를 멈춘다. 그래서 정산,
  보유시간 청산, 조정도 함께 멈춘다. 거래소에 걸려 있는 bracket만 포지션을 지킨다. 문서화된 선택이지만, 개선안의
  "어떤 halt도 축소·청산을 막으면 안 된다"와는 맞지 않는다.

---

## 1. 이 분석이 따르는 기존 결정

아래 결정은 재논의하지 않는다. 각 항목의 분류는 이 결정들을 적용한 결과다.

| 결정 | 내용 | 이 문서에 미치는 영향 |
|---|---|---|
| 시스템 점검 D3 (Thomas 2026-09-26, `CLAUDE.md`) | 첫 cohort 판정까지 새 가설·트라이얼·표시 장치를 만들지 않는다. 예외: 버그 수정, 슬리피지 실측(C2), 1h 5심볼 채굴(C3), PR7 분할 | §6·§7·§8·§9·§15 → DEFER |
| RESEARCH_EPOCH Q3=B (Thomas 2026-09-26) | 판정 규칙 완화는 에포크 경계에서만 | regime 기반 판정 변경, forward 판정 변경 → 경계까지 보류 |
| 시스템 점검 D5 / RISK_BREAKER_UNIT_RESTATEMENT (c) | 손실 브레이커 값은 슬리피지 실측 뒤에 정한다. 그때까지 값을 유지하고 LIVE_AUTONOMOUS로 올리지 않는다 | §12 → DEFER |
| REMAINING_WORK §L (2026-09-28) | E1(부정 판정 전 시간 분산), E2(후보 증거에 regime 에피소드 식별)는 첫 cohort 판정까지 보류 | §6은 E2로 이미 대기열에 있다 |
| PORTFOLIO_INDEPENDENCE Q3 (2026-09-24) | 상관 게이트는 무장 계보가 여럿 생기고 forward 표본이 한 국면을 넘으면 다시 묻는다 | §5 → DEFER |
| `CLAUDE.md` 가드레일 | Claude는 라이브 돈 경로를 실행하지 않고, 키를 다루지 않고, 라이브 거래를 켜지 않는다 | `live_*.py`·`pre_order_gate.py`·`guards.py` 변경은 모두 Thomas 검토 PR로 표시한다 |

---

## 2. 한눈에

"짓기"는 코드로 메울 수 있는 부분이다. "기다리기"는 데이터·달력 시간·결정이 메우는 부분이다.

| § | 항목 | 분류 | 짓기 | 기다리기 |
|---|---|---|---|---|
| 1 | 단계 사다리와 실행 원칙 | ALREADY_IMPLEMENTED | 불변식 테스트 2개(§3의 표) | LIVE_SCALED 진입 규칙, anti-rollback 결정 |
| 3 | Forward cohort 검증 | PARTIALLY_IMPLEMENTED | 멤버별 지표 확장. D3 예외인지부터 확인(Q1) | 판정 자체(달력 시간) |
| 4 | 라이브 슬리피지 분포 | PARTIALLY_IMPLEMENTED | 측정 스크립트에 분위수·차원 추가(C2 예외) | 표본. stop n=12, 진입 n=3이다 |
| 5 | 포트폴리오 상관·방향 노출 | DEFER | — | Q3 재개 조건. 라이브 동시 포지션 2개 |
| 6 | 전략 × Regime 평가 | DEFER | — | E2(에피소드 정의 결정), 에포크 경계 |
| 7 | 가설 중심 factory | DEFER | — | D3 |
| 8 | Stress cost 백테스트 | PARTIALLY_IMPLEMENTED | C2의 "3/10/23.5 bps 순손익" 열. D3 예외인지 확인(Q2) | P90 슬리피지(표본 없음) |
| 9 | Paper/Testnet/Live gap KPI | DEFER | 잘못된 이름의 필드 하나(버그급) | 라이브 체결 |
| 10 | 신뢰도 가중 사이징 | DEFER | — | 증거. 노출을 바꾸는 변경이다 |
| 11 | 포트폴리오 전략 vs 리스크 게이트 | ALREADY_IMPLEMENTED | — | — |
| 12 | 리스크 단위 분리 | ALREADY_IMPLEMENTED | — | D5(값 재결정) |
| 13 | Soft/Hard halt | PARTIALLY_IMPLEMENTED | — (런북은 이미 적고 있다) | pause/kill 의미 결정(Q3) |
| 14 | Health/Heartbeat | ALREADY_IMPLEMENTED | — | — |
| 15 | 운영자 대시보드 단순화 | DEFER | — | D3 |
| 16 | 연구/실행 평면 경계 | PARTIALLY_IMPLEMENTED | 현재 상태를 고정하는 테스트 | 가져오기 재배치(라이브 경로) |
| 17 | 당장 하지 않을 것 | ALREADY_IMPLEMENTED | — | — |

---

## 3. 항목별 분석

개선안이 요구한 12개 필드를 절마다 적었다. 이미 구현된 절(§11·§14·§17)은 해당 없는 필드를 합쳐 적었다.

### §1. 단계 사다리와 실행 원칙 — ALREADY_IMPLEMENTED

- **현재 구현.**
  - 사다리: `crypto/execution_stage.py`. `plan_transition`이 건너뛰기를 `STAGE_SKIP_REFUSED`로 막는다(:479–480).
    LIVE_SCALED는 `STAGE_NOT_DEFINED`("no entry rule yet", :481–482)이다.
  - 첫 기록은 SHADOW나 PAPER만 가능하다. LIVE_AUTONOMOUS에는 완료된 signed testnet 사이클이 필요하다.
  - 기록이 없거나, 변조됐거나, 승인으로 증인되지 않았으면 READ_ONLY로 읽는다 [확인].
  - 단계는 진입 guard(`live_order.evaluate_live_order_guard`)에만 들어가고, 청산 guard에는 인자가 없다 [확인].
- **이미 있는가?** 예. 원칙 여덟 개 중 여섯은 테스트로 고정돼 있다(아래 표).
- **실제 gap.**
  - (a) 거래소 **서명 쓰기** 호출을 하는 모듈을 열거해 고정하는 테스트가 없다. 지금은 `live_execution.py`와
    `testnet_execution.py` 두 곳이다. 기존 테스트는 import 그래프와 `submit_and_reconcile` 호출자를 고정한다.
  - (b) 실행 모듈이 연구 모듈을 import하지 않는다는 테스트가 없다(§16).
  - (c) 단계 anti-rollback은 `EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md`에 있다. 대조 시점에는 DRAFT였고, 같은 날 결정·구현됐다(§7).
  - (d) LIVE_SCALED 진입 규칙은 정의되지 않았다. 의도된 상태다.
- **근거.** `tests/test_mvp_runtime_crypto_execution_stage.py::test_climbing_is_one_rung_at_a_time`(:134),
  `tests/test_mvp_runtime_crypto_live_execution.py::test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`(:284)
  외 아래 표.
- **안전 영향.** (a)(b)는 테스트만 추가하므로 런타임 변화가 없다.
- **권한 영향.** 없음.
- **권고 변경.** (a)(b)를 테스트 전용 PR 하나로 만든다(§5의 PR-B).
- **영향 파일.** `tests/` 아래 새 테스트 1개.
- **필요한 테스트.** 그 테스트 자체. 새 모듈이 서명 쓰기를 하면 실패해야 한다.
- **우선순위.** P0(비용이 가장 싸다).
- **구현 권고.** 지금 진행.

#### 개선안 §21 불변식 대조

| 불변식 | 고정하는 테스트 | 상태 |
|---|---|---|
| 단계 건너뛰기 금지 | `test_mvp_runtime_crypto_execution_stage.py::test_climbing_is_one_rung_at_a_time`, `test_the_first_record_is_only_shadow_or_paper` | 고정 [확인] |
| 연구 → 라이브 자동 승격 금지 | `test_mvp_runtime_crypto_live_tier.py::test_the_lifecycle_ladder_cannot_arm_a_strategy`(:123), `test_a_recovery_moves_the_status_and_never_the_tier`(:149) | 고정 |
| 전략이 직접 실행하지 않음 | `test_mvp_runtime_crypto_live_execution.py::test_the_chokepoint_is_the_only_runtime_module_that_imports_the_executing_leg`(:308) | 고정 |
| 두 번째 라이브 chokepoint 금지 | 같은 파일 :284, `test_every_caller_of_the_venue_also_counts_the_order`(:330) | 부분 고정. 서명 쓰기 모듈 목록은 고정돼 있지 않다 → PR-B |
| PreOrderRiskGate 없는 라이브 진입 금지 | `test_mvp_runtime_crypto_pre_order_gate.py::test_no_snapshot_no_order`(:348), `test_an_entry_without_its_recorded_snapshot_is_never_sent`(:431) | 고정 |
| hot-path 재검증 없는 라이브 진입 금지 | `RISK_SNAPSHOT_STALE` 계열(`test_mvp_runtime_crypto_live_leg.py:1779`, `..._live_route.py:2037`), `test_mvp_runtime_crypto_hard_halt_egress.py::test_the_halt_is_read_at_every_submit`(:176) | 고정. 계약 문서 `HOT_PATH_PRE_EXECUTION_REVALIDATION_CONTRACT_V0.1.md`는 `PREVIEW_ONLY`이고 범용 실행기를 말한다. 크립토 레인은 60초 스냅샷 한도(`pre_order_gate.MAX_SNAPSHOT_AGE_SECONDS`)가 같은 역할을 한다 |
| 증거가 없거나 오래되면 실행 금지 | 위 staleness 테스트, `test_mvp_runtime_crypto_live_position.py::test_unreadable_account_refuses_entries_but_permits_closes`(:211) | 고정 |
| halt 중 새 노출 금지 | `test_mvp_runtime_crypto_hard_halt_egress.py::test_a_stopped_runtime_refuses_an_entry_at_the_adapter`(:157), `test_the_soft_halt_is_refused_where_entries_are_decided_not_here`(:168) | 고정 |
| halt 중 reduce-only 청산 유지 | `test_exits_and_protection_go_out_under_a_hard_halt`(:120), `test_an_exit_never_reads_the_control_state`(:142) | **어댑터 수준에서만 고정.** pause/kill에서는 청산을 **보낼 주체가 없다**(§13) |
| 거래소 조정이 우선 | `test_mvp_runtime_crypto_live_position.py::test_every_drift_shape_refuses_entry_and_is_named`(:192), `test_mvp_runtime_crypto_live_route.py::test_drift_that_bookkeeping_cannot_repair_still_halts_from_any_context`(:1128) | 고정 |
| 선택적·누락 증거가 긍정 증거가 되지 않음 | `test_mvp_runtime_crypto_promotion.py::test_unmeasured_inputs_score_zero_not_full_credit`(:734), `test_mvp_runtime_crypto_regime_routing.py::test_absent_evidence_admits` | 고정. 뒤의 테스트는 "증거가 없으면 거르지 않는다"이다. regime 필터는 거절만 하는 필터라서, 증거가 없다고 진입이 새로 열리지는 않는다 |

### §3. Forward Cohort 검증 (P0) — PARTIALLY_IMPLEMENTED

- **현재 구현.**
  - 동결 cohort: `forward_cohort.py`. 115명과 null 쌍둥이 115명, 09-23 동결 [문서].
  - 판정: `forward_confirmation.judge_forward`(:242–305). `status`, `closed_count`, `priceable_count`, `mean_net_r`,
    `active_slices`를 낸다.
  - 보고: `forward_cohort.cohort_report`(:816–864). `trade_mean_r`, `trade_lower_bound_r`, `trade_spread_floor_r`,
    `trade_floor`, `maturity`를 낸다.
  - null arm: `forward_cohort_null.py`가 타임프레임별로 real과 null을 비교한다 [확인].
- **이미 있는가?** 판정 기계장치는 있다. 개선안이 적은 12개 지표 중 멤버별로 보이는 것은 `forward_trade_count`와
  `forward_expectancy_R` 두 개뿐이다.
- **실제 gap(원인별).**
  - *행에 이미 있고 합산만 안 하는 것:* `forward_cost_R`, `forward_slippage_R`. 각 forward 행은
    `fee_cost_r`·`slippage_cost_r`·`maker_fee_cost_r`를 갖는다(`trade_plan.py:631–644`) [확인].
  - *함수는 있고 호출하지 않는 것:* win rate, PF, max drawdown R, 평균 승·패 R. `outcome_math.summarize_outcomes`와
    `lifecycle.compute_metrics`가 있지만 `cohort_report`는 부르지 않는다 [확인].
  - *입력은 있고 계산이 없는 것:* forward vs backtest gap, forward vs holdout gap. 후보에 `backtest_evidence.expectancy`와
    holdout이 있다.
  - *데이터가 없는 것:* `forward_regime_distribution`. forward·라이브 결과 행에는 regime 라벨이 없다
    (`trade_plan.py`·`forward_book.py`·`live_settlement.py`·`live_leg.py`에서 `market_regime|entry_regime`을 찾지 못함).
    이것은 E2(§L)와 같은 문제다.
- **근거.** 첫 forward 판정 6건(09-27)은 모두 CONTRADICTED 또는 UNDERPOWERED였다.
  `FORWARD_VERDICT_REGIME_EPISODES_V0.1.md` §2 [문서].
- **안전 영향.** 보고만 하는 확장이라 런타임 변화가 없다. 판정 규칙은 건드리지 않는다.
- **권한 영향.** 없음. 판정·승격·보드 순위에 새 지표를 **쓰지 않는다**는 조건에서다.
- **권고 변경.** `scripts/forward_cohort.py report`에 원인별 첫 두 묶음(비용·슬리피지 R, win rate·PF·MDD·평균 승패)과 두
  gap을 **읽기 전용 열**로 추가한다. 보드(`dashboard`)와 판정은 건드리지 않는다.
- **영향 파일.** `scripts/forward_cohort.py`. 필요하면 `crypto/outcome_math.py`의 기존 함수를 재사용한다.
- **필요한 테스트.** 합성 행에 대한 지표 값, 행이 없거나 파싱할 수 없는 멤버의 표시(0이 아니라 "없음"), 판정 결과가
  바뀌지 않음.
- **우선순위.** P0.
- **구현 권고.** **Q1의 답을 받은 뒤에 진행한다.** 이것이 D3가 멈춘 "표시 장치"인지, 아니면 cohort를 **읽는** 도구인지는
  Thomas가 정한다.

### §4. 라이브 슬리피지 분포 (P0) — PARTIALLY_IMPLEMENTED

- **현재 구현.**
  - `scripts/measure_live_slippage.py`(#576, #1009 수정)는 읽기 전용이다. 의도 가격과 체결 가격을 진입·stop 다리별로
    비교하고, stop은 출처(전략·프로브)별로 median·mean·worst를 낸다 [확인].
  - 비용 상수: `cost.DEFAULT_SLIPPAGE_BPS = 3.0`(INHERITED), `DEFAULT_STOP_SLIPPAGE_BPS = 1.4`(프로브 실측).
  - 라이브 진입 문은 호가 충격이 모델을 넘으면 거절한다(`live_entry.MAX_ENTRY_SLIPPAGE_BPS`).
- **이미 있는가?** 도구는 있다. P75·P90·P95는 없다. 차원은 출처(전략·프로브)만 있고, 심볼·변동성·스프레드·크기로는
  나누지 않는다.
- **실제 gap.**
  - **코드 쪽은 작다.** 분위수와 심볼별 분리는 몇 줄이다.
  - **데이터 쪽이 병목이다.** 2026-09-28 실측은 stop n=12(전략 3, 프로브 9), 진입 n=3(모두 BTCUSDT)이었다 [문서, §F8 재실행].
    n=12에서 P90은 사실상 최댓값 하나이고, P95는 정의조차 되지 않는다.
  - PAPER 단계에서는 새 진입·stop이 생기지 않는다. 표본을 늘리는 수단은 canary와 프로브 배치뿐이고, 둘 다 Thomas가
    내는 실주문이다.
  - 개선안이 말하는 `f(symbol, volatility, spread, size, liquidity, order_type)` 모델은 표본 수백 건이 쌓인 뒤의 일이다.
- **근거.** `REMAINING_WORK.md` §F8. 중앙값 후보의 손익분기가 4.3 bps이고, 모델은 3.0 bps를 가정한다.
- **안전 영향.** 스크립트는 읽기 전용이다. 상수는 바꾸지 않는다. 상수 변경은 D5 결정과 함께 한다.
- **권한 영향.** 없음.
- **권고 변경.**
  - 스크립트 출력에 n, P75/P90/P95를 추가한다. n < 20이면 분위수 대신 "표본 부족"이라고 쓴다.
  - 심볼·주문 유형별 분리를 추가한다.
  - 행에 기록된 필드로 계산할 수 있으면 진입 시점의 `market_impact.impact_bps`(봉인값)와 실현 슬리피지를 나란히 둔다.
- **영향 파일.** `scripts/measure_live_slippage.py`, `tests/test_measure_live_slippage.py`.
- **필요한 테스트.** 분위수 계산, 표본 부족 표시, 심볼 분리.
- **우선순위.** P0. 다만 효과는 표본이 생긴 뒤에야 나온다.
- **구현 권고.** 지금 진행할 수 있다(D3의 C2 예외). **표본을 늘릴지는 운영 결정이다.** 프로브 배치를 더 돌릴지는
  Thomas가 정한다. 이 문서는 권하지도 막지도 않는다.

### §5. 포트폴리오 상관·방향 노출 (P0) — DEFER

- **현재 구현.**
  - 라이브:
    - `live_position.MAX_LIVE_CONCURRENT_POSITIONS = 2`, `MAX_LIVE_POSITIONS_PER_SYMBOL = 1`.
    - 등록 예산의 `max_open_notional_usdt`(`live_order.claim_caps_problem`, 진행 중 진입까지 합산).
    - 거래당 위험은 자산의 1%(`live_sizing.RISK_PER_TRADE_FRACTION`) [확인].
  - Paper: `paper.MAX_DIRECTIONAL_SKEW`(심볼당 한도에서 도출)는 한쪽으로 기울어진 장부를 거절만 하고, 교정 방향 진입은
    막지 않는다 [확인].
  - 보고: `independence.py`가 같은 방향 평균 상관과 `effective_bets`를 계산한다. 퍼널(#974)과 LIVE 요청문(#976)에
    나온다.
- **이미 있는가?** 부분적으로. 개선안의 `correlated_long_risk_R`류 필드는 없다.
- **실제 gap.**
  - 라이브에 방향 노출 한도가 없다. 그러나 이는 **명시적으로 결정된 것**이다. `paper.py:240–245`는 동시 포지션이 2개면
    라이브 skew 한도가 무의미하거나 용량을 반으로 줄일 뿐이고, 한 방향 위험은 Thomas가 정한 `max_open_notional_usdt`가
    다스린다고 적는다.
  - 지금 라이브 최대 같은 방향 노출은 2포지션 × 1R ≈ 2R이다 [추정].
  - `REDUCE` 판정은 이 저장소에 없다. pre-order gate는 모든 검사를 통과하느냐 아니냐뿐이다. REDUCE는 주문 크기를
    바꾸는 일이므로 사이징(§10)이고, 개선안 §20의 "노출을 바꾸는가" 질문에 해당한다.
- **근거.** `PORTFOLIO_INDEPENDENCE_V0.1.md` Q3: 상관 게이트는 무장 계보가 여럿 생기고 forward 표본이 한 국면을 넘으면
  다시 묻는다. 지금 무장 계보는 0이다 [문서, 09-24].
- **안전 영향.** 지금 지으면 효과가 없고(한도가 묶이지 않음), 라이브 경로 코드만 늘어난다.
- **권한 영향.** 거절만 하는 게이트라면 없다. REDUCE를 넣으면 사이징 권한이 된다.
- **권고 변경.** 없음. 한 가지만 기록한다. **LIVE_SCALED 진입 규칙이나 `MAX_LIVE_CONCURRENT_POSITIONS` 상향을 결정할 때,
  같은 방향 R 한도를 그 결정의 전제 조건으로 함께 묻는다.** 동시 포지션이 늘어나는 순간 이 한도가 의미를 갖기 때문이다.
- **영향 파일 / 필요한 테스트.** 해당 없음(보류).
- **우선순위.** 개선안은 P0이지만, 이 문서의 판단은 **LIVE_SCALED의 선행 조건**이다.
- **구현 권고.** DEFER.

### §6. 전략 × Regime 평가 (P1) — DEFER

- **현재 구현.**
  - 라벨: `features.classify_market_regime`. TREND_UP, TREND_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY, UNCLEAR [확인].
  - 후보 증거: `factory`의 `regime_breakdown`. `per_regime: {regime: {trades, total_r}}`, `regimes_traded`,
    `profitable_regime_count`.
  - 점수: `robustness._regime_breadth`(가중치 0.20).
  - 승격: 증거가 풀 항목의 `regime_evidence`로 복사된다.
  - 런타임 필터: `trade_plan.regime_admits`. 거래 10건 이상에서 `total_r ≤ 0`인 regime만 거절하고(`REGIME_EXCLUDED`),
    불확실하면 허용한다. paper 라우팅과 forward book이 쓰고, 라이브는 paper의 경로를 물려받는다.
- **이미 있는가?** 개선안이 "ALLOW/BLOCK 우선"이라고 한 런타임 형태는 이미 있다. 거절만 하는 필터다.
- **실제 gap.**
  - regime별 지표는 거래 수와 합계 R뿐이다. expectancy, win rate, PF, MDD, 비용은 없다.
  - forward·라이브 행에 regime이 없다.
  - 라벨 수를 셀 뿐 독립 에피소드는 세지 않는다.
- **근거.** `FORWARD_VERDICT_REGIME_EPISODES_V0.1.md`(RECORD 09-27): 판정 6건이 BTC 일봉 에피소드 3–5개 위에 있고, 하락
  추세일은 0일이다. 에피소드 수는 정의에 따라 몇 배씩 움직인다(4h holdout TREND_UP 3–19).
- **안전 영향.** 기록 스키마가 바뀐다(닫힌 스키마). regime을 판정에 쓰면 판정 규칙이 바뀐다.
- **권한 영향.** regime으로 신호를 거절하는 것은 이미 있다. REDUCE는 §10의 사이징이다.
- **권고 변경.** 새 항목을 만들지 않는다. **REMAINING_WORK §L의 E2가 이 항목의 대기열 형태다.** 선행 결정(에피소드 정의:
  macro proxy, gap 허용치, 최소 길이)과 트리거(첫 cohort 판정 또는 에포크 경계)를 그대로 따른다. 트리거가 오면 regime별
  지표 확장을 E2와 같은 PR에서 한다.
- **영향 파일.** (트리거 뒤) `crypto/factory.py`의 `regime_breakdown`, `robustness.py`, 결과 행 스키마.
- **필요한 테스트.** (트리거 뒤) `tests/test_mvp_runtime_crypto_regime_routing.py` 확장.
- **우선순위.** P1.
- **구현 권고.** DEFER(D3, E2).

### §7. 가설 중심 Strategy Factory (P1) — DEFER

- **현재 구현.**
  - 가설 트라이얼: `HYPOTHESIS_TRIAL_V0.1.md`(IMPLEMENTED 09-24, #977–#981), `factory` `TRIAL_DERIVATION`,
    `MAX_OPEN_TRIALS = 4`, `forward_trial.py`(동전 던지기 쌍둥이 포함). 트라이얼은 승격할 수 없다.
  - ablation: `factory.ablate_hypothesis`.
  - 다중검정: `robustness.selection_adjusted_z`(Bonferroni).
  - null: `null_control.py`, `forward_cohort_null.py`.
  - 탐색 폭: `EXPLORATION_BUDGET_V0.1.md`(Q2b #975). 가문 소진: `FAMILY_EXHAUSTION_V0.1.md`(DECIDED 09-26).
  - proposer 제안에는 `rationale`이 있다.
- **이미 있는가?** "가설 → 근거 → 트라이얼 → holdout → forward"의 흐름은 트라이얼 경로로 있다.
- **실제 gap.**
  - `StrategyTemplate`(`factory.py:506–512`)에 가설·근거 필드가 없다. 가문의 근거는 entry builder의 주석에만 있다.
  - `hypothesis_id`는 `crypto/` 어디에도 없다 [확인].
  - 트라이얼 행의 `trial_source`는 proposer의 `rationale`을 담지 않는다. `proposal_id`를 거쳐야 찾을 수 있다.
  - 가문 소진 퍼널 절(30일 CONFIRMED)은 결정됐지만 아직 지어지지 않았다.
- **근거.** 시스템 점검 C1: 가설을 하나 더할 때마다 다중검정 부담이 커진다. 반복 판독 시 zero-edge 4h 멤버 48개에서 연간
  거짓 확정이 약 2.4건이다 [문서, #967].
- **안전 영향.** 없음(연구 평면).
- **권한 영향.** 없음.
- **권고 변경.** D3가 풀릴 때 두 가지를 먼저 본다. 템플릿에 `rationale`을 한 줄 싣는 것, 그리고 트라이얼 행에 `rationale`을
  복사하는 것. 둘 다 새 기계장치가 아니라 기존 필드의 전달이다. 가문 소진 절은 DECIDED 상태의 구현 대기열(STATUS.md)에
  이미 있으므로 그쪽을 따른다.
- **영향 파일.** (해제 뒤) `crypto/factory.py`, 트라이얼 행 스키마.
- **필요한 테스트.** (해제 뒤) `tests/test_mvp_runtime_crypto_hypothesis_trial.py`.
- **우선순위.** P1.
- **구현 권고.** DEFER(D3). "candidate 수 ↓, 품질 ↑"라는 목표는 선택 보정·탐색 예산·가문 소진으로 이미 추구하고 있다.

### §8. Stress Cost 백테스트 (P1) — PARTIALLY_IMPLEMENTED

- **현재 구현.**
  - 후보 증거 `cost_summary`에 `total_slippage_cost_r` 등 비용 성분이 R로 기록된다.
  - 그래서 비율 *r*에서 다시 매기는 값은 `net − slippage × (r/3 − 1)`로 정확히 구할 수 있다(§F8의 산식) [문서].
  - 코드 안의 다중 비율 경로는 `candidate_ranking.expectancy_at` 하나이고, 수수료만 다시 매긴다 [확인].
  - §F8에는 손으로 만든 표가 있다: 중앙값 후보의 순손익 @3/10/23.5 bps, 손익분기 4.3 bps.
- **이미 있는가?** 계산 재료는 있다. 후보별 `expectancy_stress_R` 필드와, 그것을 보드나 LIVE 요청문에 싣는 경로는 없다
  (`stress|sensitivity|breakeven|cost_scenario`를 찾음: 무관한 것만 나옴).
- **실제 gap.**
  - 시스템 점검 C2가 이미 설계했다. cohort 보드와 LIVE 요청문에 "3/10/23.5 bps에서의 순손익" 열을 추가하는 것이다.
    `docs/history/2026-09-28-slippage-instrument-archives.md`는 이것을 "별도 변경"으로 남겼다.
  - 개선안의 STRESS = "관측 P90 슬리피지"는 지금 정의할 수 없다(§4, n=12).
- **안전 영향.** 보고만 한다. 승격 판정에 쓰지 않는다(개선안도 자동 승격을 금한다).
- **권한 영향.** 없음.
- **권고 변경.**
  - 슬리피지 재가격 함수 하나를 `candidate_ranking.expectancy_at` 옆에 둔다(순수 함수).
  - 후보별 손익분기 bps를 계산한다.
  - `promotion._live_context_notes`(LIVE 요청문)에 고정 비율 세 개의 순손익과 손익분기를 한 줄로 싣는다.
  - STRESS 비율은 관측 분위수가 아니라 고정값 3/10/23.5로 둔다. 관측 분포가 생기면 그때 바꾼다.
- **영향 파일.** `crypto/candidate_ranking.py`, `crypto/promotion.py`, 테스트.
- **필요한 테스트.** 재가격이 §F8 산식과 일치하는지, 슬리피지 성분이 없는 후보는 "없음"으로 표시되는지(0이 아니다),
  LIVE 요청문 문구.
- **우선순위.** P1. 비용 모델이 load-bearing이라는 §F8의 결론 때문에 가치가 높다.
- **구현 권고.** **Q2의 답을 받은 뒤에 진행한다.** C2 예외("슬리피지 실측")가 이 열까지 덮는지, 아니면 D3가 멈춘 표시
  장치인지는 Thomas가 정한다.

### §9. Paper / Testnet / Live gap KPI (P1) — DEFER

- **현재 구현.**
  - 라이브 결과 행: 실제 체결 `entry_price`·`exit_price`, `stop_price`, stop 청산의 `stop_slippage_bps`
    (`live_settlement.build_live_outcome_record`).
  - 진입 의도 가격 `intended_price`(#585).
  - 진입 시점 봉인값: `market_impact.impact_bps`, `round_trip_cost_r`, `round_trip_cost_r_at_book`(`live_entry.py`).
  - 창 단위 수수료 포함 손익: `live_pnl.venue_daily_realized_net`.
- **이미 있는가?** 기대값과 실현값이 양쪽에 모두 기록된다. 그러나 거래 단위로 짝지어지지 않는다.
- **실제 gap.**
  - 거래별 실제 수수료가 없다. `live_leg.py:1297`에서 `fees_included: False`, `pnl_source: "venue_fills_gross"`이다.
  - expectancy gap(backtest↔paper, paper↔live, testnet↔*)이 어디에도 없다. `testnet_evidence.py`는 경로 무결성만
    기록한다.
  - **이름이 틀린 필드가 하나 있다.** `lifecycle.compute_strategy_performance`의 `live_vs_backtest_win_rate_drop`
    (`lifecycle.py:154`)은 실제로는 **paper** 결과로 계산된다. `cycle.py:294`에서 `read_outcomes`(paper)가 넘어간다 [확인].
    이 값이 probation을 결정한다(`lifecycle.py:262`).
- **근거.** PAPER 단계에서는 라이브 체결이 생기지 않는다 [문서, §F8 재실행]. 라이브 결과는 28행이다(08-23) [문서].
- **안전 영향.** 필드 이름을 바꾸면 기록 키가 바뀌므로 읽는 쪽이 있는지 먼저 봐야 한다.
- **권한 영향.** 없음.
- **권고 변경.**
  - (a) 이름: 최소한 docstring과 주석에 "paper vs backtest"라고 적는다. 키 이름 변경은 소비자를 조사한 뒤에 한다.
    버그 수정 범주다(D3 예외).
  - (b) KPI 보드: 라이브 표본이 생기고 D3가 풀린 뒤에 한다.
- **영향 파일.** (a) `crypto/lifecycle.py`.
- **필요한 테스트.** (a) 없음(주석), 또는 키를 바꾸면 소비자 테스트.
- **우선순위.** (a) 낮음, (b) P1.
- **구현 권고.** (a)는 PR-A에 포함할 수 있다. (b)는 DEFER.

### §10. 신뢰도 가중 포지션 사이징 (P2) — DEFER

- **현재 구현.**
  - `live_sizing.size_live_order` = min(자산 × 1% / 단위 위험, 예산 한도) × `volatility_size_multiplier`. 변동성 배수는
    [0.25, 1.0] 범위라 줄이기만 한다.
  - 한도: `live_budget.HARD_CEILING_USDT = 500`, 주문별·미결제·일손실 USDT 한도. 계보별 허용치(`live_allowance`)는 크기를
    줄이지 않고 **막는다**.
- **이미 있는가?** 신뢰도·regime·drawdown 배수는 없다.
- **실제 gap.** 개선안의 공식 전체가 없다. 참고로 거래당 위험 숫자가 둘이다. 등록된 `risk_limits.risk_per_trade`는
  drawdown %를 R로 바꾸는 데만 쓰이고, 사이징은 모듈 상수 `RISK_PER_TRADE_FRACTION = 0.01`을 쓴다. 지금은 둘 다 1%라
  어긋나지 않는다 [확인].
- **근거.** 신뢰도를 계산할 forward·라이브 증거가 없다(무장 계보 0).
- **안전 영향.** **노출을 바꾼다.** 배수가 1 이하로만 묶이더라도 사이징 경로는 라이브 돈 경로다.
- **권한 영향.** 개선안 §20에 따르면 노출을 바꿀 수 있으므로 별도 리스크·거버넌스 검토 대상이다.
- **권고 변경.** 없음. 개선안도 "충분한 증거가 쌓인 후 연구"라고 적었다.
- **영향 파일 / 필요한 테스트.** 해당 없음.
- **우선순위.** P2.
- **구현 권고.** DEFER. 거래당 위험 숫자가 둘인 것은, 한쪽이 바뀌는 날 어긋난다. D5(브레이커 단위 재결정) 때 함께 보도록
  기록만 한다.

### §11. 포트폴리오 전략과 런타임 리스크 게이트의 구분 — ALREADY_IMPLEMENTED

- **현재 구현.**
  - 횡단면 피처 `xs_rank_pct`, `xs_excess_roc_4`, `xs_dispersion_ratio`(`features.py:129–156`)는 **단일 심볼의 진입
    조건**으로만 쓰인다(`factory._xs_momentum_*`, `_xs_reversion_*`, `_rel_strength_*`).
  - 런타임은 거절하거나 reduce-only 주문만 낸다. 라이브는 one-way 모드이고 `positionSide`가 없다.
  - bracket과 청산 다리는 `reduceOnly`/`closePosition`이다.
  - `top_k|bottom_k|market_neutral|pairs|hedge` 구성 코드는 없다 [확인].
- **이미 있는가?** 예. 전략이 신호를 내지 않은 포지션을 런타임이 여는 경로는 없다.
- **실제 gap.** 없음.
- **근거.** `paper.py:288–290`("it can only ever decline. It never opens a position, never flips one"),
  `tests/test_mvp_runtime_crypto_paper.py::test_route_direction_conflict_fails_closed`.
- **안전·권한 영향.** 없음.
- **권고 변경.** 없음. `PortfolioStrategy`는 시장중립 전략이 필요해질 때 별도 결정으로 다룬다.
- **우선순위 / 구현 권고.** 해당 없음.

### §12. 리스크 단위 분리 — ALREADY_IMPLEMENTED (값은 D5 대기)

- **현재 구현.** 개선안의 네 영역이 이미 서로 다른 단위를 쓴다.
  - 전략 리스크(R): `guards.py:47–51`의 일·주 손실, 연속 손실, drawdown. drawdown %는 R로 환산된다
    (`_drawdown_limit_r`). 계보별 허용치(`live_allowance`)도 R이다.
  - 자본 리스크(USDT): `live_budget`의 주문·미결제·일손실 한도, 상한 500 USDT.
  - 인프라 리스크(횟수): `live_order.MAX_CONSECUTIVE_API_ERRORS = 5`(읽기·쓰기 따로),
    `MAX_CONSECUTIVE_BRACKET_FAILURES = 5`. 데이터 health는 `guards.run_data_health_check`가 그 사이클만 막는다.
  - 포트폴리오 노출: USDT 미결제 한도(§5).
- **이미 있는가?** 예.
- **실제 gap.**
  - 인프라 브레이커는 **비율**이 아니라 **연속 횟수**다. 개선안의 "error rate"와 다르지만, 연속 횟수가 더 보수적으로
    작동한다(성공 한 번이 비율을 희석하지 못한다).
  - R의 의미가 2026-07-30에 gross에서 net으로 바뀌었는데, 임계값은 다시 정하지 않았다.
- **근거.** `RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md`(PARTIALLY DECIDED 09-26): (c) 슬리피지 실측이 먼저이고, 값은 하나도
  바뀌지 않았다.
- **안전 영향.** 값을 바꾸면 브레이커가 바뀐다. 이 문서는 바꾸지 않는다.
- **권한 영향.** 없음.
- **권고 변경.** 없음. 브레이커별 단위를 운영 문서에 표로 적는 일은 §15와 함께 한다.
- **우선순위 / 구현 권고.** D5를 따른다.

### §13. Soft Halt / Hard Halt — PARTIALLY_IMPLEMENTED

- **현재 구현.** `runtime/mvp_runtime/control.py:68–92`: `HALT_SOFT`, `HALT_HARD`(Thomas 결정 7·47). 두 수준 모두
  런타임을 ACTIVE로 둔다.
  - **SOFT:** 진입 guard에서만 거절한다. 청산·보호·보유시간 청산·testnet 리허설은 계속된다.
  - **HARD:** 어댑터에서 reduceOnly/closePosition이 아닌 모든 주문을 거절한다(`live_execution.control_refusal`).
    운영자만 풀 수 있다.
  - 풀 권한이 없는 문도 조일 수는 있다(`_HALT_RANK`). 읽을 수 없는 수준은 HARD로 읽는다 [확인].
- **이미 있는가?** 개선안의 SOFT/HARD 정의는 이미 구현돼 있고 테스트로 고정돼 있다
  (`tests/test_mvp_runtime_crypto_hard_halt_egress.py`).
- **실제 gap — 개선안의 원칙과 어긋나는 곳.**
  - `/pause`와 `/kill`은 halt 수준이 아니라 **런타임 모드**다. 이 모드에서 `scheduler.run_due`는 due fire를 **모두**
    건너뛴다(`scheduler.py:2537–2544`). 리스크 레인도 예외가 아니다.
  - 그래서 정산, 보호 재확인, 보유시간 청산, 조정이 모두 멈춘다. `control.py:69–73`이 이를 명시한다.
  - `scripts/emergency_close.py`는 "HARD halt + 런타임 ACTIVE"를 요구하므로, KILLED 상태에서는 비상 청산도 거절된다
    [확인].
  - 이때 포지션을 지키는 것은 **거래소에 이미 걸려 있는 bracket뿐**이다.
  - 개선안 §13의 "어떤 halt도 기존 위험 포지션의 안전한 축소/청산을 막으면 안 된다"는 SOFT/HARD에는 맞고, pause/kill에는
    맞지 않는다.
  - 이것은 결함이라기보다 **문서화된 선택**이다. `halt_trading`이 바로 "포지션 관리를 유지하는 halt"로 만들어졌고,
    런북도 이를 적고 있다(`docs/DEPLOYMENT.md:389–391`: kill·pause는 정산·보호 재확인·보유시간 청산을 `/resume`까지
    멈춘다) [확인].
  - 남는 위험은 운영자 쪽이다. 긴급할 때 손이 가는 명령이 `/kill`이다.
  - 관련 제안: `PROTECTION_UNKNOWN_ESCALATION_V0.1.md`(D2 = 런타임이 스스로 HARD로 조이는 권한). 대조 시점에는 DRAFT였고,
    같은 날 결정·구현됐다(§7).
- **근거.** 위 인용. `tests/test_mvp_runtime_scheduler.py::test_killed_or_paused_skips_execution`(:565),
  `tests/test_mvp_runtime_crypto_emergency_close.py::test_no_ask_without_the_hard_halt_with_the_runtime_active`(:218).
- **안전 영향.** kill의 의미를 바꾸면 "전부 멈춤"이라는 가장 강한 정지 수단이 약해진다. 양쪽 모두 비용이 있다.
- **권한 영향.** kill 중에 청산을 계속하려면 kill 상태에서도 주문을 내는 경로가 생긴다. 이것은 권한 변경이다.
- **권고 변경.**
  - 코드는 바꾸지 않는다. 개선안이 요구한 "운영자 수준의 명확화"는 런북에 이미 있다.
  - kill 의미 자체를 바꿀지는 Q3이다.
- **영향 파일 / 필요한 테스트.** 해당 없음.
- **우선순위.** Q3의 답에 달렸다.
- **구현 권고.** Thomas(Q3).

### §14. Health / Watchdog — ALREADY_IMPLEMENTED

- **현재 구현.**
  - 프로세스: compose healthcheck.
  - 루프: `runtime/mvp_runtime/heartbeat.py`, 레인별 파일. 주기의 3배, 최소 300초가 지나면 stale이다.
  - fire 중 BUSY 표시: #1028 `heartbeat.busy_marker`.
  - 멈춘 fire 감시견: #1029 `fire_watchdog.py`. 마감 600/120/120초, 넘으면 프로세스를 재시작한다.
  - 호스트: `scripts/ops/health_watch.sh`(10분 주기, 디스크 포함).
  - 거래 사이클: `live_readiness.trading_cycle_recent`(`NO_RECENT_CYCLE`).
  - 시장 데이터: 사이클 안에서 `guards`의 `stale_market_data`, readiness의 `market_data_ready` 행.
- **이미 있는가?** 개선안의 네 층(Process / Scheduler / Market Data / Trading Cycle)이 모두 따로 있다.
- **실제 gap.**
  - 시장 데이터 heartbeat는 사이클과 **독립적이지 않다.** 마지막 fire의 기록에서 읽는다. 다만 사이클이 멈추면
    감시견과 `NO_RECENT_CYCLE`이 먼저 잡으므로, 독립 heartbeat가 더 알려 줄 것은 적다 [추정].
  - `RISK_LANE_WATCHDOG_V0.1.md`의 상태 줄은 여전히 "구현 PR 두 개 진행 중"이다. #1028·#1029는 머지됐다. 이 문서의 범위가
    아니라서 고치지 않았다.
- **안전·권한 영향.** 없음.
- **권고 변경.** 없음.
- **우선순위 / 구현 권고.** 해당 없음.

### §15. 운영자 대시보드 단순화 — DEFER

- **현재 구현.** 한 화면이 아니라 read 문의 다섯 읽기로 나뉘어 있다(`read_bridge.py:63–85`).
  - `crypto_status`: `dashboard.build_status`.
  - `crypto_readiness`: `live_readiness.build_readiness`.
  - `crypto_funds`, `runtime_status`, `heartbeat`.
  - README "Current stage"(#1000).
- **이미 있는가?** 개선안의 필드 대부분이 어딘가에 있다.
  - 단계, 킬 스위치, 시장 데이터, 조정, 오늘 주문 수, 무장 전략 수, forward cohort.
- **실제 gap.**
  - 한 화면이 없다.
  - "Risk Used", 라이브 일손익(행 하나만 있음), P90 슬리피지, 알림 목록이 없다. 알림은 변화 시 보내는 텔레그램 메시지다.
  - **오독 위험이 하나 있다.** `crypto_status`의 `open_positions`와 `directional_lean`은 **paper** 장부를 읽는다
    (`dashboard.py:321–348`). 라이브 장부는 readiness 쪽에만 있다. 같은 종류의 오독이 이미 한 번 있었다(readiness
    보드의 프로세스 범위, #685).
- **근거.** 위 인용.
- **안전 영향.** 표시만 하므로 없다. 다만 오독은 운영 판단에 영향을 준다.
- **권한 영향.** 없음.
- **권고 변경.** 한 화면 보드는 D3가 풀린 뒤에 만든다. 그 전에 할 수 있는 것은 **라벨**뿐이다. `open_positions`에
  "paper"라고 적는 것은 새 표시 장치가 아니라 잘못 읽힐 수 있는 기존 표시의 정정이다. 다만 Hermes가 이 필드를 어떻게
  말하는지 먼저 확인해야 한다(Hermes 도구 이름과 콘솔 동사는 다르다).
- **영향 파일.** (라벨) `crypto/dashboard.py`.
- **필요한 테스트.** (라벨) 보드 문구 테스트.
- **우선순위.** P2(보드), 라벨은 낮음.
- **구현 권고.** DEFER. 라벨은 PR-A 후보.

### §16. 연구 평면 / 실행 평면 경계 — PARTIALLY_IMPLEMENTED

- **현재 구현.**
  - 승인된 전략은 해시된 산출물로 넘어간다. `crypto/strategy_artifact.py`(`strategy_artifact.v1`, Thomas 결정 31–34).
    드리프트가 있으면 풀 전체를 거절하고, 스탬프된 항목만 LIVE로 무장한다. 해시는 키 없는 해시라서 인증은 Thomas 승인이
    한다.
  - 레이어 테스트: `tests/test_mvp_runtime_crypto_layers.py`(PR7a). **방향**만 고정한다. factory·robustness·proposer는
    "strategy" 층에 있고 실행층보다 아래다. 그래서 연구 코드가 주문 코드를 import하는 것은 막히고, 반대 방향은 허용된다.
- **이미 있는가?** 산출물 경계는 있다. import 경계는 반쪽이다.
- **실제 gap.**
  - 실행·리스크 모듈(`live_order`, `live_execution`, `live_leg`, `live_entry`, `pre_order_gate`, `live_budget`, `guards`,
    `live_reconcile`)은 factory·robustness·proposer·null_control을 직접도 전이로도 import하지 않는다. 함수 안의 import까지
    포함한 AST 전이 폐포로 확인했다 [확인].
  - 그러나 **`live_route`**(라이브 오케스트레이션)는 전이로 닿는다. `live_route.py:126`이 `promotion.live_arm_problem`을
    가져온다. `promotion.py:37`은 `forward_book`·`forward_confirmation`을 가져오고, `forward_confirmation.py:59`는
    `robustness`를 가져온다. `promotion` → `judgement_fingerprint` → `factory` 경로도 있다 [확인]. `breaker_watch`는
    `pool` → `candidate_ranking`을 통해 factory·robustness에 닿는다 [확인].
  - 이를 금지하는 테스트는 없다.
- **근거.** 위 인용.
- **안전 영향.** 테스트 추가는 런타임 변화가 없다. import를 재배치하면 라이브 경로 코드가 바뀐다.
- **권한 영향.** 없음.
- **권고 변경.**
  - (a) 실행·리스크 모듈 목록이 연구 모듈을 전이로도 import하지 않는다는 테스트를 추가한다. `live_route`와 `breaker_watch`는
    **알려진 예외로 이름을 적어** 고정한다(PR-B). 예외 목록이 늘면 테스트가 실패한다.
  - (b) `live_arm_problem`을 연구 의존이 없는 모듈로 옮기는 일은 라이브 경로 변경이다. PR7 분할 규율(레이어 테스트, 기록
    비교)을 따라 Thomas 검토로 한다.
- **영향 파일.** (a) `tests/` 새 테스트 1개, 또는 `test_mvp_runtime_crypto_layers.py` 확장.
- **필요한 테스트.** (a) 자체.
- **우선순위.** P2(개선안), 다만 (a)는 싸다.
- **구현 권고.** (a) 지금 진행. (b) PR7 대기열에 기록.

### §17. 당장 추가하지 않을 것 — ALREADY_IMPLEMENTED

개선안의 목록(지표 대량 추가, ML·RL, LLM 진입 방향 결정, 후보 대폭 확대, 1m/5m, 레버리지·유니버스 확대)은 D3·Q3=B와 같은
방향이다. 추가로 할 일은 없다.

---

## 4. Thomas 결정 항목

**결정 (Thomas 2026-09-30): Q1·Q2·Q3 모두 권고대로.**

- **Q1 → D3 밖.** forward cohort를 읽는 스크립트 열은 D3가 멈춘 표시 장치가 아니다. 조건은 §3에 적은 그대로다: 판정·보드·
  순위에 쓰지 않는다. 그래서 PR-D는 `cohort_report`(보드와 walk가 함께 읽는다)에 키를 더하지 않고, 스크립트 쪽에서 계산한다.
- **Q2 → C2 예외.** 고정 비율에서의 순손익과 손익분기는 슬리피지 실측(C2)의 일부로 본다. LIVE 요청문에 표시만 하고, 승격
  판정에는 쓰지 않는다.
- **Q3 → A.** `/pause`·`/kill`은 지금처럼 포지션 관리까지 멈춘다. 런북이 이미 그렇게 적고 있으므로
  (`docs/DEPLOYMENT.md:389–391`) 코드도 문서도 바꾸지 않는다.

아래는 결정 전에 적은 질문과 권고다.

- **Q1. forward cohort 지표 확장(§3)은 D3의 "표시 장치"인가?** 판정·보드·순위에 쓰지 않는 읽기 전용 스크립트 열이다.
  **권고:** D3 밖으로 본다. D3의 목적은 다중검정 부담과 연구 노력의 분산을 막는 것이고, 이 열들은 가설을 늘리지 않고
  이미 있는 cohort를 더 잘 읽게 한다.
- **Q2. "3/10/23.5 bps 순손익" 열과 손익분기(§8)는 C2 예외에 드는가?** 시스템 점검 C2가 설계한 바로 그 열이다.
  **권고:** 예외로 본다. §F8이 보여 준 대로 비용 가정이 load-bearing이고, 이 열이 없으면 LIVE 요청문이 그 사실을 말하지
  않는다.
- **Q3. `/pause`·`/kill`이 포지션 관리까지 멈추는 현재 의미를 유지하는가(§13)?**
  - **A.** 유지하고 런북에 적는다.
  - **B.** kill 중에도 정산·보호 재확인·보유시간 청산만 도는 좁은 경로를 둔다. 새 권한이다.
  - **권고:** A. 거래소 bracket이 남아 있고, 포지션을 줄이며 멈추는 수단(`halt_trading hard`)이 이미 있다. B는 "전부
    멈춤"이라는 수단을 약하게 만든다.

---

## 5. 구현 PR 분할 (권고)

런타임에 영향이 없는 것부터, 작은 단위로 나눈다. 새 Contract·Schema·Gate는 만들지 않는다.

| PR | 내용 | 런타임 영향 | 선행 조건 |
|---|---|---|---|
| PR-A | `lifecycle`의 `live_vs_backtest_win_rate_drop`이 paper에서 계산된다는 주석(§9a) | 없음 | 없음 |
| PR-B | 불변식 테스트 2개: 서명 쓰기 모듈 목록 고정(§1a), 실행 모듈의 연구 import 금지와 예외 이름(§16a) | 없음(테스트 전용) | 없음 |
| PR-C | `measure_live_slippage.py`: n·분위수(표본 부족 표시)·심볼·주문 유형 분리(§4) | 없음(읽기 전용 스크립트) | 없음(C2 예외) |
| PR-D | `scripts/forward_cohort.py report`: 비용·슬리피지 R, win rate·PF·MDD·평균 승패, backtest·holdout gap(§3) | 없음 | Q1 |
| PR-E | 슬리피지 재가격 순수 함수, 손익분기, LIVE 요청문 한 줄(§8) | 요청문 문구만 바뀜 | Q2 |

각 PR은 해당 영역 테스트를 먼저 돌리고, 그다음 전체 스위트를 돌린다(개선안 §21).
`dashboard.open_positions`의 "paper" 라벨(§15)은 Hermes가 그 필드를 어떻게 전하는지 확인한 뒤 PR-A에 넣을지 정한다.

**이 목록에 없는 것:** §5·§6·§7·§9b·§10·§15 보드. 각 항목의 "기다리기" 조건이 차면, 그때 이 문서를 근거로 다시 연다.

---

## 6. 확인하지 못한 것

- 라이브 호스트의 현재 cohort 행 수와 슬리피지 표본. 모두 09-27/09-28 문서 값이다. 호스트에서 확인한 것은 단계뿐이다.
- Hermes가 `crypto_status`의 paper 필드를 운영자에게 어떻게 말하는지.
- 전략의 수익성 자체. 이 문서는 개선안과 코드의 차이를 봤다.

---

## 7. 대조 이후 바뀐 것 (2026-09-30, 같은 날)

위 본문은 `70328a10` 기준이다. 인용한 줄 번호도 그 커밋의 것이다. 이 문서가 머지되기 전에 main에서 바뀐 것을 적는다.

- **§5의 PR.**
  - PR-B는 #1051로 머지됐다. 서명 쓰기 모듈 목록과, 실행 쪽 모듈이 연구 코드에 닿는 범위가 테스트로 고정됐다.
  - PR-C는 #1052로 머지됐다. 슬리피지 도구가 분위수와 심볼별 분리를 낸다. 라이브 표본은 여전히 stop n=12라서 분위수는
    표시되지 않는다.
  - PR-A는 #1050으로 머지됐다. `lifecycle`의 docstring이 그 승률 하락 지표가 paper와 backtest의 비교임을 적는다.
- **§1 (c).** `EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md`는 결정되고(Thomas 2026-09-30, #1041) 구현됐다(#1055): 단계 기록이
  해시 체인 장부의 끝이 되고, 백업에 들어가지 않는 앵커가 이를 보증한다. 배포 뒤 운영 단계 두 가지가 남는다(그 제안서의
  상태 줄).
- **§13.** `PROTECTION_UNKNOWN_ESCALATION_V0.1.md`는 결정되고 구현됐다(#1054): 포지션별 시계, 30분에 신규 진입 보류,
  60분에 HARD halt. 런타임이 스스로 HARD로 조이는 첫 경로다. pause/kill의 의미는 바뀌지 않았으므로 Q3는 그대로 남는다.
- **§1·§16.** #1049가 송신 모듈 목록과 스크립트 분류를 따로 고정했다. PR-B의 테스트와 함께 돈다.
- **§5의 PR (결정 뒤).** PR-D는 #1067로 머지됐다: `scripts/forward_cohort.py report --detail`. `cohort_report`와
  `runtime/`은 건드리지 않았다. PR-E는 #1069로 머지됐다: `candidate_ranking.net_at_slippage`·`slippage_breakeven_bps`와
  LIVE 요청문의 네 번째 note. §5의 다섯 PR이 모두 main에 있다.
- **§8 정정.** "비율 *r*에서 다시 매기는 값을 정확히 구할 수 있다"는 §F8(08-06)의 산식은 stop 슬리피지가 분리되기(08-11)
  전의 것이다. 지금 `total_slippage_cost_r`은 진입 다리(`slippage_bps`)와 stop 다리(`stop_slippage_bps`)가 섞인 합이고,
  stop 몫은 따로 기록되지 않는다(수수료의 `total_maker_fee_cost_r`에 해당하는 필드가 없다). 09-30 호스트 저장소의 후보
  3,546건 중 분리 전 기록은 1,948건이고, 1,593건은 두 비율이 다르다(stop 1.4가 1,417건, 12.0이 176건) [확인]. LIVE를
  요청할 수 있는 후보는 모두 뒤쪽이다. 그래서 PR-E(#1069)는 분리된 기록에 값 하나가 아니라 범위를 낸다. 범위를 없애려면
  백테스트(`crypto/backtest.py`, #1068로 factory에서 분리됨)가 stop 몫을 기록해야 하는데, 기록 형태를 바꾸는 일이라
  PR-E에 넣지 않는다.
