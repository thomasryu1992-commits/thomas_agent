# 제안서 상태 — 자동 생성, 손으로 고치지 않는다

`python scripts/build_proposal_status.py`로 다시 만든다. 원본은 각 제안서 머리의 `**상태:**` 줄이고, `tests/test_design_record_lifecycle.py`가 그 줄이 닫힌 어휘를 벗어나거나 이 파일이 원본과 어긋나면 실패한다. 상태가 바뀌면 제안서의 상태 줄을 고치고 이 파일을 다시 만든다.

날짜는 결정일이다. `DRAFT`와 `RECORD`는 작성일이고, 결정 기록을 찾지 못한 것은 구현·가동일이다(요약에 그렇게 적는다).

제안서 **37**건: `DRAFT` 3 · `PARTIALLY DECIDED` 8 · `DECIDED` 3 · `IMPLEMENTED` 21 · `SUPERSEDED` 1 · `RECORD` 1

## Thomas 결정 대기 (11)

결정이 하나라도 남은 제안서. 오래 기다린 것부터 적는다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md](CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md) | `PARTIALLY DECIDED` | 2026-07-25 | D1–D3 결정·구현(task registry, frontdesk, provider). D4(standing grant, `approval.v0.3`)는 결정 대기, D5는 D4를 기다린다. |
| [APPROVAL_CONVERSATION_V0.1.md](APPROVAL_CONVERSATION_V0.1.md) | `DRAFT` | 2026-07-28 | §6의 V1–V4 결정 대기. 결정 기록도, 권고한 옵션 B(승인 명령을 제안하는 턴)의 구현도 없다(2026-09-26 확인). |
| [CONTROL_LANE_SEPARATION_V0.1.md](CONTROL_LANE_SEPARATION_V0.1.md) | `PARTIALLY DECIDED` | 2026-07-29 | K2′(단계 경계 halt peek)는 결정·구현(`operator.peek_for_halt`). K4(실행 중인 분석 중단)·K5는 결정 대기, K1은 결정 기록이 없다(`docs/BUILD_HISTORY.md`, `docs/REMAINING_WORK.md`). |
| [EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md](EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md) | `PARTIALLY DECIDED` | 2026-08-08 | §5 계보별 LIVE 허용치는 구현됐다(`crypto/live_allowance.py`: 연속 2패 또는 누적 −2.0R이면 강등; 결정 인용도 구현일 기록도 찾지 못해 날짜는 작성일이다). 제안한 셋째 기준(예산 1/4)은 빠졌고 그 기록이 없다. §8의 B(복귀 경로)·D(연속손실 브레이커 래치)는 결정 대기. |
| [AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR_V0.1.md](AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR_V0.1.md) | `PARTIALLY DECIDED` | 2026-08-09 | Part 1(OBSERVATION/LIVE 티어)은 2026-08-09 구현(#648, `crypto/live_tier.py`; 승인 인용은 찾지 못했다). Part 2(관찰 티어 자동 설치)는 결정도 구현도 없다. |
| [WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md](WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md) | `DRAFT` | 2026-08-12 | PR-2(판정 활성화) 결정 대기. PR-1(기록 전용)은 구현됐고, 결정 근거인 §4 측정 결과는 아직 기록되지 않았다. 날짜는 추정이다(FACTORY_ABLATION 결정 이후 작성). |
| [TEMPLATE_RSI_DRAW_MASS_V0.1.md](TEMPLATE_RSI_DRAW_MASS_V0.1.md) | `DRAFT` | 2026-08-17 | Thomas 결정 대기. 권고(현상 유지 + §5 재독)의 재독·결정 기록이 없다. |
| [COST_GATE_RESET_THE_RECORD_V0.1.md](COST_GATE_RESET_THE_RECORD_V0.1.md) | `PARTIALLY DECIDED` | 2026-09-26 | §6-2(펀딩 게이팅)는 게이트 없이 보고만 하는 현재 구현(`crypto/cost.py`, "Reported, not gated")을 추인했다(Thomas 2026-09-26). §6-1(오염된 폐기 판정 11개)·§6-3(반사실 레지스트리 측정)은 결정 기록이 없다. |
| [RESEARCH_EPOCH_V0.1.md](RESEARCH_EPOCH_V0.1.md) | `PARTIALLY DECIDED` | 2026-09-26 | Q1·Q2(판정 규칙 지문과 그 표시)는 #972·#976으로 구현. Q3는 B로 결정(Thomas 2026-09-26, 경계 주기는 미정). Q4(다음 cohort에 판정 지문)는 다음 cohort를 동결할 때 정한다. |
| [RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md](RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md) | `PARTIALLY DECIDED` | 2026-09-26 | (c) 순서 결정: 슬리피지 실측이 먼저다. (a)(b)(d)는 실측 뒤에 정하고, 그때까지 현재 값을 유지하며 LIVE_AUTONOMOUS로 올리지 않는다(Thomas 2026-09-26, 시스템 점검 D5). **값은 하나도 바뀌지 않았다**. |
| [SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md](SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md) | `PARTIALLY DECIDED` | 2026-09-26 | §5의 D1·D2·D3·D4·D7·D8 채택, D6 보류(현상 유지). D5는 슬리피지 실측 뒤에 결정한다(§5 뒤 "결정" 절). 단계 0은 #980, 단계 1의 선행 조건 없는 항목은 #982–#989로 구현됐다. |

## 결정됨 — 구현 남음 (3)

결정은 끝났고 결정된 것이 아직 다 지어지지 않았다. 결정이 만든 구현 대기열이다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [EQUITY_PERP_LANE_V0.1.md](EQUITY_PERP_LANE_V0.1.md) | `DECIDED` | 2026-08-03 | ① 팩터/디스퍼전 재정의 ② 스프레드 v1 제외(2026-08-03), S0 규제 기록(2026-08-04, 잠정). S1은 2026-08-04부터 운영 중이고 S2(a)는 2026-08-09 완료(§8b). S2(b)는 데이터 깊이 때문에 아직 평가할 수 없고 S3–S5는 미착수다(`docs/REMAINING_WORK.md` §H). S3 이후 실주문은 S0의 확정 승격이라는 별도 결정 전에는 열리지 않는다. |
| [NAVER_BLOG_CONTENT_LANE_V0.1.md](NAVER_BLOG_CONTENT_LANE_V0.1.md) | `DECIDED` | 2026-08-30 | 결정은 모두 내려졌다(A안·network env 2026-08-09; 파일 쓰기, 주간 `content_ideation` 스케줄, MVP 용례 확장, 키워드 규칙, URL 기록 2026-08-30). Phase 1은 구현됐고 남은 구현은 Phase 4 순위 추적이다 (`docs/REMAINING_WORK.md` §J, 이 문서 하단의 체크리스트). |
| [FAMILY_EXHAUSTION_V0.1.md](FAMILY_EXHAUSTION_V0.1.md) | `DECIDED` | 2026-09-26 | Q1 B·Q2·Q3 결정, §6 확인 완료(1h는 5심볼 모두가 의도). 남은 구현: Q1 B의 퍼널 family 소진 절(30일 CONFIRMED·마지막 CONFIRMED)과 1h 5심볼 풀링. |

## 구현됨 (21)

결정되고 지어졌다. 문서는 결정의 근거 기록으로 남는다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [HERMES_AGENT_SWITCH_V0.1.md](HERMES_AGENT_SWITCH_V0.1.md) | `IMPLEMENTED` | 2026-07-31 | S1·S3 채택·출시(#387), S2 거부. S4(자금 스냅샷)는 아래 표가 적은 뒤에 출시됐다 (#755: 스케줄러가 쓰고 read 문의 `crypto_funds`가 보여 준다). 레인은 policy 1.4.0에 조문화됐다. |
| [GATE0_CANNOT_BE_SATISFIED_V0.1.md](GATE0_CANNOT_BE_SATISFIED_V0.1.md) | `IMPLEMENTED` | 2026-08-03 | Gate 0의 런타임 강제와 운영자 확인이 #473으로 제거됐다(`crypto/live_entry.py` 2b). 실제 돈 문을 넓힌 변경이다. 저장소에 승인 인용이 없었고, Thomas가 2026-09-26에 당시 승인했음을 확인했다. |
| [EQUITY_PERP_S1_COLLECTOR_V0.1.md](EQUITY_PERP_S1_COLLECTOR_V0.1.md) | `IMPLEMENTED` | 2026-08-04 | 구현·운영 중. `crypto/market_data.py`의 `hyperliquid` 수집기가 2026-08-04부터 매시간 `candle_archive`로 돈다(`docs/REMAINING_WORK.md` §H, H0에 첫날 결함과 수정). 날짜는 가동일이다. |
| [CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md](CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md) | `IMPLEMENTED` | 2026-08-10 | D4 채택·구현(PR-C). 아래 원문은 D5·D6을 미결로 적었지만 둘 다 이후 구현됐다 (`crypto_data_review`·`crypto_propose`의 모델 호출이 `pipeline-worker`로 위임되어 스케줄러에 모델 자격증명이 없다 — `docker-compose.yml` scheduler 블록 주석). D5·D6의 결정 기록은 찾지 못했다(2026-09-26 확인). |
| [CREDENTIAL_PLANE_SEPARATION_V0.1.md](CREDENTIAL_PLANE_SEPARATION_V0.1.md) | `IMPLEMENTED` | 2026-08-10 | D1·D2 채택, D3 방향 승인. PR-A(pipeline-worker, 문 전환)와 PR-B(스케줄러 Naver env 제거) 구현. |
| [FORWARD_EVIDENCE_CONFIRMATION_V0.1.md](FORWARD_EVIDENCE_CONFIRMATION_V0.1.md) | `IMPLEMENTED` | 2026-08-11 | §5-1·§5-2·§5-3 모두 Thomas 2026-08-11 결정·구현. §5-1은 `crypto/forward_confirmation.py`("Thomas approved #690 §5-1 on 2026-08-11"), §5-2·§5-3은 `crypto/pool_admission.py` ("Thomas's 5-2 decision (2026-08-11)", "Thomas 5-3, 2026-08-11" — OBSERVATION 진입 기준). forward 증거의 출처는 2026-08-29에 per-strategy forward book으로 개정됐다. |
| [STOP_SLIPPAGE_PROBE_V0.1.md](STOP_SLIPPAGE_PROBE_V0.1.md) | `IMPLEMENTED` | 2026-08-11 | §5 승인(Thomas)·구현·실행됨. `scripts/run_slippage_probe.py`, `crypto/probe.py`(배치 3 결정 Thomas 2026-08-18). 측정 결과(n=10, 중앙값 1.07 bps)로 `cost.DEFAULT_STOP_SLIPPAGE_BPS`가 2026-08-21에 1.4로 정해졌다(`crypto/tunables.py`). |
| [FACTORY_ABLATION_V0.1.md](FACTORY_ABLATION_V0.1.md) | `IMPLEMENTED` | 2026-08-12 | §3 제안대로 승인(Thomas), `factory.ablate_hypothesis`로 구현. |
| [FACTORY_FIRE_PROCESS_SEPARATION_V0.1.md](FACTORY_FIRE_PROCESS_SEPARATION_V0.1.md) | `IMPLEMENTED` | 2026-08-17 | §3 제안대로 승인(Thomas), 같은 날 구현(#726): 팩토리 발사를 fork 자식으로 옮기고 부모가 수거한다. 적용 범위는 `crypto_factory`만. |
| [FUNDING_ZSCORE_TIME_BASE_V0.1.md](FUNDING_ZSCORE_TIME_BASE_V0.1.md) | `IMPLEMENTED` | 2026-08-17 | B안 결정(Thomas), 같은 날 구현(funding z를 event space로). |
| [SCHEDULER_LANE_SPLIT_V0.1.md](SCHEDULER_LANE_SPLIT_V0.1.md) | `IMPLEMENTED` | 2026-08-19 | §8의 D1–D3 승인(Thomas). PR-A(#733, `--lane`)와 PR-B(scheduler=risk, scheduler-maint 신설) 배포. pass budget은 D3대로 유지했고, §7의 14일 수용 측정은 기록되지 않았다. |
| [FORWARD_SLICE_WIDTH_ARTIFACT_V0.1.md](FORWARD_SLICE_WIDTH_ARTIFACT_V0.1.md) | `IMPLEMENTED` | 2026-08-21 | §8-1 승인(Thomas), #747로 구현. §8-2는 해당 없음(#741 유지). 구현은 §3을 한 군데 벗어났다 — §9에 기록. |
| [LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md](LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md) | `IMPLEMENTED` | 2026-08-24 | 구현·실행됨. `crypto/live_correction.py`, 첫 정정 레코드 `live_corr_1710c5ca552d3e88c668`가 2026-08-24T14:20Z에 실행됐다(`docs/REMAINING_WORK.md` 헤더, §C). 날짜는 실행일이다. |
| [FORWARD_SLICE_WIDTH_FOR_FORWARD_V0.1.md](FORWARD_SLICE_WIDTH_FOR_FORWARD_V0.1.md) | `IMPLEMENTED` | 2026-08-30 | option B (14 days), `forward_confirmation.FORWARD_SLICE_WIDTH_DAYS`. |
| [HERMES_AGENT_DISPATCH_V0.1.md](HERMES_AGENT_DISPATCH_V0.1.md) | `IMPLEMENTED` | 2026-08-30 | 문, P3 allowlist, 키 없음, 귀속은 출시됐고 §6-6 정책 블록(policy 1.3.0)과 $50/일 알림(`dispatch_spend`)이 2026-08-30에 이행됐다. 열린 결정은 없다(무료 티어라 알림은 휴면). |
| [FORWARD_COHORT_OFF_POOL_V0.1.md](FORWARD_COHORT_OFF_POOL_V0.1.md) | `IMPLEMENTED` | 2026-09-23 | Decision 1 (Phase 1) and Decision 2 (option A, screen only); built in #948/#949, null arm #960/#961. Option B reopens once the null arm has measured the false-confirmation rate. |
| [EXPLORATION_BUDGET_V0.1.md](EXPLORATION_BUDGET_V0.1.md) | `IMPLEMENTED` | 2026-09-24 | 권고대로 결정(아래 "결정" 절). Q2 b(폭)는 #975로 구현, Q1(고정 비율)은 기각, Q3(OI 가중)은 OI 계보의 forward 판정 뒤 재질문. |
| [FORWARD_UNDERPOWERED_V0.1.md](FORWARD_UNDERPOWERED_V0.1.md) | `IMPLEMENTED` | 2026-09-24 | option A (the sign split), built in the same PR as this record (#964). |
| [HYPOTHESIS_TRIAL_V0.1.md](HYPOTHESIS_TRIAL_V0.1.md) | `IMPLEMENTED` | 2026-09-24 | C안 결정, PR1–PR4 구현(#977·#978·#979·#981). 선언형 family는 결정대로 만들지 않았다. |
| [PORTFOLIO_INDEPENDENCE_V0.1.md](PORTFOLIO_INDEPENDENCE_V0.1.md) | `IMPLEMENTED` | 2026-09-24 | Q1 A(퍼널의 독립 베팅 절) #974, Q2 B(LIVE 요청문의 forward 상관·N_eff) #976 구현. Q3(상관 게이트)는 조건이 차면 재질문. |
| [SEQUENTIAL_FORWARD_TEST_V0.1.md](SEQUENTIAL_FORWARD_TEST_V0.1.md) | `IMPLEMENTED` | 2026-09-24 | Q1(조기 판정 전제) 기각, Q2 A 구현(#971 첫 판정 시각·보드 누적, #976 LIVE 요청 문구). Q3·Q4는 조건부 보류. |

## 대체됨 (1)

후속 문서가 대신한다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [LIVE_OUTCOME_CORRECTION_RECORD_V0.1.md](LIVE_OUTCOME_CORRECTION_RECORD_V0.1.md) | `SUPERSEDED` | 2026-08-23 | `LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md`가 대신한다. 이 설계는 구현된 적 없다. |

## 측정 기록 (1)

결정할 것이 없는 관측 기록.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [EQUITY_PERP_S1_MEASUREMENTS_V0.1.md](EQUITY_PERP_S1_MEASUREMENTS_V0.1.md) | `RECORD` | 2026-08-05 | 2026-08-04~05 측정 기록. 설계 제안이 아니라 **관측값**이며, 재현 방법을 함께 적는다. |
