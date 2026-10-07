# 제안서 상태 — 자동 생성, 손으로 고치지 않는다

`python scripts/build_proposal_status.py`로 다시 만든다. 원본은 각 제안서 머리의 `**상태:**` 줄이고, `tests/test_design_record_lifecycle.py`가 그 줄이 닫힌 어휘를 벗어나거나 이 파일이 원본과 어긋나면 실패한다. 상태가 바뀌면 제안서의 상태 줄을 고치고 이 파일을 다시 만든다.

날짜는 결정일이다. `DRAFT`와 `RECORD`는 작성일이고, 결정 기록을 찾지 못한 것은 구현·가동일이다(요약에 그렇게 적는다).

제안서 **58**건: `DRAFT` 2 · `PARTIALLY DECIDED` 4 · `DECIDED` 10 · `IMPLEMENTED` 36 · `SUPERSEDED` 1 · `RECORD` 5

## Thomas 결정 대기 (6)

결정이 하나라도 남은 제안서. 오래 기다린 것부터 적는다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md](WALK_FORWARD_TEMPORAL_STABILITY_V0.1.md) | `DRAFT` | 2026-08-12 | PR-2(판정 활성화)는 Thomas의 답이 아니라 §4 사전 등록 보고서를 기다린다 (`scripts/walk_forward_stability_report.py`, 읽기 전용, D3 측정 예외). 판별력이 없으면 PR-2 없이 닫는다(Thomas 2026-10-06, `SYSTEM_SCORECARD_V0.1.md` Q3). PR-1(기록 전용)은 구현됐다. 날짜는 추정이다(FACTORY_ABLATION 결정 이후 작성). |
| [SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md](SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md) | `PARTIALLY DECIDED` | 2026-09-26 | §5의 D1·D2·D3·D4·D7·D8 채택(D8 범위는 2026-10-06에 정리: `general.specialist` 제외), D6 보류(현상 유지). D5는 첫 forward cohort 판정(2027-03-22) 때 묻고 그때까지 LIVE_AUTONOMOUS로 올리지 않는다(Thomas 2026-10-06, `SYSTEM_SCORECARD_V0.1.md` Q6; `RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md`). 단계 0은 #980, 단계 1의 선행 조건 없는 항목은 #982–#989로 구현됐다. D3의 범위와 종료 조건은 2026-10-01에 다시 정했다("결정 (Thomas 2026-10-01)" 절: 측정·표시 허용, 종료는 1차 cohort 마감 2027-03-22). |
| [EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md](EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md) | `PARTIALLY DECIDED` | 2026-10-06 | §5 계보별 LIVE 허용치는 구현(`crypto/live_allowance.py`), 셋째 기준(예산 1/4)은 폐기 (2026-09-26). §8 D는 (i)로 결정·구현: 연속손실 브레이커의 래치는 의도이고, `crypto/guards.py`의 판정 옆에 그 이유와 푸는 방법을 적었다(Thomas 2026-10-06, 아래 결정 절). §8 B(복귀 경로), R값 없는 라이브 손실, 허용치의 net R 정렬은 첫 LIVE 무장 때 다룬다(지금 무장 0, 단계 PAPER). |
| [PERSONAL_BRANDING_EXPANSION_HYPOTHESIS_V0.1.md](PERSONAL_BRANDING_EXPANSION_HYPOTHESIS_V0.1.md) | `PARTIALLY DECIDED` | 2026-10-06 | B1(0단계 측정)은 결정되어 지어졌다: 10-11 주(10-12 보고)부터 주간 리뷰가 잰다. B2(1단계 시작 조건)·B3(관문 수치)은 §4 그대로 결정됐고(Thomas 2026-10-06), 주간 리뷰가 시작 조건 충족 여부를 계산해 보인다. 열린 항목은 B4 수익 기준이며, 2단계 관문을 넘은 시점에 정한다. |
| [RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md](RISK_BREAKER_UNIT_RESTATEMENT_V0.1.md) | `PARTIALLY DECIDED` | 2026-10-06 | (a)(b)(d)는 첫 forward cohort 판정(2027-03-22) 때 묻는다. 그때까지 크립토 라이브는 LIVE_AUTONOMOUS로 올리지 않고 현재 값을 유지한다(Thomas 2026-10-06, `SYSTEM_SCORECARD_V0.1.md` Q6). 관문이 "슬리피지 실측 뒤"에서 "첫 판정 때"로 바뀌어 순환이 없어졌다. **값은 하나도 바뀌지 않았다.** |
| [TOTAL_ASSET_ALLOCATION_V0.1.md](TOTAL_ASSET_ALLOCATION_V0.1.md) | `DRAFT` | 2026-10-07 | 중립형 목표 비중(주식 40·채권 37·금 13·BTC 5·현금 5), 5/25 밴드 월 점검 리밸런싱, 계좌 배치를 제안한다. Q1–Q5 미결. 코드·스키마·상수·스케줄 변경 없음. |

## 결정됨 — 구현 남음 (10)

결정은 끝났고 결정된 것이 아직 다 지어지지 않았다. 결정이 만든 구현 대기열이다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [EQUITY_PERP_LANE_V0.1.md](EQUITY_PERP_LANE_V0.1.md) | `DECIDED` | 2026-08-03 | ① 팩터/디스퍼전 재정의 ② 스프레드 v1 제외(2026-08-03), S0 규제 기록(2026-08-04, 잠정). S1은 2026-08-04부터 운영 중이고 S2(a)는 2026-08-09 완료(§8b). S2(b)는 데이터 깊이 때문에 아직 평가할 수 없고 S3–S5는 미착수다(`docs/REMAINING_WORK.md` §H). S3 이후 실주문은 S0의 확정 승격이라는 별도 결정 전에는 열리지 않는다. |
| [FAMILY_EXHAUSTION_V0.1.md](FAMILY_EXHAUSTION_V0.1.md) | `DECIDED` | 2026-09-26 | Q1 B·Q2·Q3 결정, §6 확인 완료(1h는 5심볼 모두가 의도). 남은 구현: Q1 B의 퍼널 family 소진 절(30일 CONFIRMED·마지막 CONFIRMED). 1h 5심볼 민팅은 이미 돌고 있었다(§6 정정, 2026-09-26). |
| [RISK_LANE_WATCHDOG_V0.1.md](RISK_LANE_WATCHDOG_V0.1.md) | `DECIDED` | 2026-09-29 | 타임아웃 시 프로세스 재시작, 마감값 600/120/120 s(Thomas). 구현 PR 두 개(§7) 진행 중. |
| [SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md](SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md) | `DECIDED` | 2026-09-30 | D1 C·D2·D3 B→A·D4 예(Thomas): LIVE는 FORWARD_CONFIRMED 필수, forward 기준을 `observed_lineages`로 보정, 재사용 홀드아웃 표시는 버그 수정으로 지금 구현, 검증 슬라이스는 에포크 경계. 남은 구현: ① 문 변경, ② B 표시. ② A는 `REMAINING_WORK.md` §L. |
| [CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md](CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md) | `DECIDED` | 2026-10-01 | S1·S2·S3 권고대로(Thomas). S1: 첫 forward cohort 판정 때 factory mint 비중을 1d로 기울인다(손실을 줄이는 선택이지 엣지 주장이 아님). S2: 쌍둥이 기준 판정은 에포크 경계에서 `REMAINING_WORK.md` §L E1과 함께 다룬다. S3: maker 진입은 지금 다시 열지 않는다. 0단계(`report --arms`)는 #1088로 구현됐고, S1·S2의 실행은 각 관문(첫 판정, 에포크 경계)을 기다린다. 이 문서가 바꾼 판정 규칙·보드·상수는 없다. |
| [CRYPTO_ARCHIVE_BACKFILL_V0.1.md](CRYPTO_ARCHIVE_BACKFILL_V0.1.md) | `DECIDED` | 2026-10-03 | R1–R4 권고대로(Thomas). R1: OI 측정·백필은 지금, 피처 전환은 첫 cohort 판정 때 S1과 함께. R2: 롱숏은 게이트를 명시적 결정으로 고정한 뒤 백필, 생성은 2027-03-22 이후. R3: 아카이브는 런타임 밖에서 받는다. R4: 호가 깊이는 D3 이후. 단계 1은 #1119로, R2의 게이트 고정(`positioning_store.MINTING_DECIDED`=False)은 이번 PR로 구현됐다. OI 백필 도구는 #1121(단계 2). 롱숏 백필은 저장 방식 때문에 보류(아래 2026-10-03 추가 결정). 남은 일: OI import 실행, 첫 판정 때 단계 3. |
| [EXPANSION_READINESS_REVIEW_V0.1.md](EXPANSION_READINESS_REVIEW_V0.1.md) | `DECIDED` | 2026-10-06 | Q1–Q4 모두 결정(Thomas 2026-10-06; Q3은 같은 날 앞선 크론 유지 결정을 확인). Q1: 서버 스크립트를 로컬 git으로 관리하는 부분은 지어졌고, `.bak` 삭제와 볼트 사본 통합은 Thomas 확인을 기다린다(§5). Q2: 매일 프롬프트를 순서만 남기고 날짜 붙은 규칙을 두 스킬로 옮겼다. Q3: Tistory 생산 경로는 크론으로 확정됐다(Thomas 2026-10-06). 10-31에는 런타임 Tistory 프로파일을 남길지만 정하고, 그 판단에 쓸 비교 도구를 지었다. Q4: 자산 통합 보드는 `holdings/`가 바이낸스 스냅샷 파일을 읽어 합산한다(패키지 import 없음). P2를 지을 때 KIS 보드가 켜진 뒤 구현한다. 남은 구현은 Q1의 Thomas 실행분과 Q4다. |
| [RESEARCH_EPOCH_V0.1.md](RESEARCH_EPOCH_V0.1.md) | `DECIDED` | 2026-10-06 | Q1·Q2 구현(#972·#976). Q3 B(2026-09-26), 경계는 cohort 마감마다: 첫 경계는 1차 cohort 마감 2027-03-22, 이후 각 cohort의 마감(동결 + 180일)이다(Thomas 2026-10-06, `SYSTEM_SCORECARD_V0.1.md` Q5). Q4는 C로 정리됐다. 남은 구현: 두 판정의 병렬 표시 — 규칙이 처음 바뀌는 경계에서 짓는다. |
| [RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md](RESEARCH_FORWARD_THROUGHPUT_ANALYSIS_V0.1.md) | `DECIDED` | 2026-10-06 | P0-1(D3 범위) 결정(2026-10-01), P1-1(형제 규칙) 결정·구현(#1096·#1100). 나머지는 권고 순서대로 결정했고(Thomas 2026-10-06), D3가 허용하는 측정·표시 넷을 지었다: P0-2 주석 정정(#1137), P0-3 진입 거절 사유 카운터(#1139), P1-2 보고서 행 색인(#1138), P1-3 쌍 차이 표시(#1140). 남은 것은 관문이 있는 둘이다: P1-4 계층 판정 제안서(2027-03-22 전), P2(판정 뒤). |
| [MULTI_ASSET_EXPANSION_V0.1.md](MULTI_ASSET_EXPANSION_V0.1.md) | `DECIDED` | 2026-10-07 | D1–D4 결정(2026-10-02): (A) 자산 관리 먼저, 옵션은 조건부, 읽기 전용 보드는 연구 일시 중지 중 허용. **2026-10-07 Thomas: 첫 계좌를 한국투자증권에서 토스증권으로 바꾼다(토스만 쓴다).** 부록 B(토스증권 규제 기록)가 완성됐다(본인 계좌 조회 운영 가능, 강도 잠정, 허용 IP 등록됨). 토스 피드(`holdings/toss_account.py`)가 지어졌고, KIS 피드와 compose의 KIS 변수는 같은 PR에서 통째로 제거됐다. 부록 A(KIS)는 결정 기록으로 남는다. P1-a·P1-b의 렌더·저장·`/holdings` verb·scheduler-maint 배선은 그대로 쓴다. 켜는 것은 배포와 `holdings_refresh` 일정 등록(Thomas)을 기다린다. 키는 `.env`에 들어갔다(2026-10-07). §6 D2 비교 기준 비준은 P4 때 한다. IV–RV 측정은 연구 일시 중지의 예외로 허용됐다 (2026-10-06, 아래 결정 절). |

## 구현됨 (36)

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
| [NAVER_BLOG_CONTENT_LANE_V0.1.md](NAVER_BLOG_CONTENT_LANE_V0.1.md) | `IMPLEMENTED` | 2026-09-27 | 결정된 것은 지어졌고(A안·network env 2026-08-09; 파일 쓰기, 주간 `content_ideation` 스케줄, MVP 용례 확장, 키워드 규칙, URL 기록 2026-08-30), 2026-09-27 Thomas가 레인을 보류했다. 주간 행은 비활성(삭제 아님)이고 Phase 4 순위 추적은 구현 대기열에서 빠졌다. 발화 3회(09-06·13·20)가 모두 선정 단계에서 `NO_ELIGIBLE_KEYWORD`로 끝나 패키지가 한 번도 만들어지지 않았고, 원인은 고정 시드라는 키워드 원천이다. 글은 필요할 때 요청으로 만든다. 경위·재개 조건·확인된 결함은 `docs/REMAINING_WORK.md` §J. |
| [CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md](CRYPTO_REFACTOR_AND_MODULARIZATION_PLAN_V0.1.md) | `IMPLEMENTED` | 2026-09-30 | D-1~D-5 권고대로(Thomas) 결정, 2026-10-02 구현 완료로 닫는다. 구현: §S PR-02·03(#1049), PR-04(#1059), PR-05(#1060), PR-07(#1064), PR-08~10(#1068·#1071·#1072, 리플레이·템플릿 공간·생성기 분리), PR-11(#1075), PR-12(#1077), PR-13(#1079), PR-S3(#1082, 승격 문 CAS, 행동 변경: 풀이 바뀌었으면 설치 거부), PR-14(#1085), PR-15(#1086·#1087), PR-16(#1090·#1093); §M-2e(#1097); L-2.3(#1098, `live_promotion`→`live_evidence` 별칭); J-5.1(#1099, 연 포지션에 `cycle_id` 기록); §O(#1101·#1106); §K(#1102·#1104, K-1 행동 불변); 계획 밖 재수출 후속(#1107·#1108). PR-06 실측은 §K-1에 있다. 런타임을 바꾼 PR은 매번 candidate로 배포해 첫 파이어를 관측했다(마지막 candidate-1108, 2026-10-02). `factory`는 5,477줄에서 1,746줄로, `live_order`는 2,041줄에서 755줄로, `live_leg`는 1,931줄에서 1,441줄로 줄었다(2026-10-02 main 기준). 남은 재수출 65개(6개 모듈)는 `tests/test_mvp_runtime_crypto_reexport_roster.py`가 고정하며, 시험이 이름으로 고정한 계약(`live_leg`의 `live_leg_results` 이름 전부, `live_readiness.readiness_data`, 상태 루트의 `STATE_REL`·`state_dir`)과 `factory`의 템플릿 빌더다. `pool`은 설계상 파사드다. `live_route`·`breaker_watch`가 닿는 연구 모듈은 `backtest`·`robustness` 둘이다. 남은 둘은 구현 항목이 아니다: PR-13·14·15의 서명 테스트넷 사이클 1회는 SIGNED_TESTNET 전이 뒤에 돌리고(2026-09-30에는 단계가 PAPER라 가드가 설계대로 거부), §Q는 D3 정지(2027-03-22까지)로 대상이 아니다. 단계별 경위는 `docs/history/`. |
| [CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md](CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md) | `IMPLEMENTED` | 2026-09-30 | Q1·Q2·Q3 권고대로(Thomas) 결정·구현: forward cohort 읽기 열은 D3 밖(PR-D #1067), 슬리피지 재가격 열은 C2 예외(PR-E #1069), pause/kill 의미는 유지(변경 없음). PR-A·B·C는 #1050·#1051·#1052. 외부 개선안 16개 절 중 이미 구현 5, 부분 구현 5, 결정에 따라 보류 6이고, 권한을 새로 만드는 항목은 없다. 보류 6건은 각 항목의 조건(첫 cohort 판정, 슬리피지 실측, 에포크 경계)이 차면 이 문서를 근거로 다시 연다. |
| [EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md](EXECUTION_STAGE_ANTI_ROLLBACK_V0.1.md) | `IMPLEMENTED` | 2026-09-30 | D1 a·D2·D3 a·D4 권고대로(Thomas) 구현. 배포 뒤 운영 단계 두 가지가 남는다: BOOTSTRAP(PAPER) 승인 1회로 장부 시작, 호스트 백업 스크립트에 앵커 제외 설치(§Implementation). |
| [PROTECTION_UNKNOWN_ESCALATION_V0.1.md](PROTECTION_UNKNOWN_ESCALATION_V0.1.md) | `IMPLEMENTED` | 2026-09-30 | D1–D4 권고대로(Thomas) 구현(`crypto/protection_watch.py`). 한 가지를 바꿨다: U1은 사이클 정지(`record["halt"]`)가 아니라 신규 진입 보류다. 사이클 정지는 다른 심볼 포지션의 관리까지 건너뛰기 때문이다(§Implementation). 보드 한 줄(§2.5)은 D3 표시 동결로 미구현. |
| [FORWARD_COHORT_EXPANSION_V0.1.md](FORWARD_COHORT_EXPANSION_V0.1.md) | `IMPLEMENTED` | 2026-10-01 | N1–N4 권고대로(Thomas). N1: 2차 동결은 D3 밖이다. N2: 28일마다 운영자가 수동으로 동결하되, 1h 선정 행이 약 200일을 넘기 전이어야 한다. N3: Decision 2 B 아래 K는 같은 문맥의 모든 동결 cohort를 합산한다. N4: 1차 cohort는 2027-03-22(동결 + 180일)에 한 번 판정하고 닫으며, 이후 cohort도 동결 + 180일이다. Decision 2 B는 그 마감 판정과 쌍둥이 확정률을 놓고 다시 묻는다. 2차 cohort `fwd_cohort_31fbbb4ff2b2491fedd8`(82명, 17개 문맥)와 그 쌍둥이 82명을 2026-10-01 04:04 UTC에 동결했다. 다음 동결은 2026-10-29, 2차 마감은 2027-03-30. |
| [FORWARD_COHORT_SIBLING_RULE_V0.1.md](FORWARD_COHORT_SIBLING_RULE_V0.1.md) | `IMPLEMENTED` | 2026-10-01 | S1–S4 권고대로(Thomas). 구현은 아래 "구현" 절. 배포 뒤 10-29 동결부터 적용된다. - S1: A(열린 cohort의 키는 다음 동결에서 제외) + B(2차 형제는 읽는 시점에 표시). - S2: 키는 보유 cohort 마감(동결 + 180일)에 풀린다. - S3: 키 정의는 그대로 둔다. - S4: 풀 점유 계보와의 형제는 범위 밖이다. |
| [PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md](PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md) | `IMPLEMENTED` | 2026-10-05 | Q1–Q4 모두 권고대로(Thomas): 카나리 rung 없음 유지, 코드 상수 2차 권위 없음, 어댑터 키 읽기 유지, D3 중 ResearchSignal v2·ID 체인 보류. §D의 P2 테스트 3건(paper 격리, 증거는 단계 증인이 아님, 안전 불변식 lane)을 같은 PR에서 지었다. 코드·스키마·정책 변경 없음. ResearchSignal·ID 체인은 첫 cohort 판정 뒤 이 문서를 근거로 다시 연다. |
| [APPROVAL_CONVERSATION_V0.1.md](APPROVAL_CONVERSATION_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | V1–V4 모두 하지 않기로 결정했다(Thomas 2026-10-06, 아래 결정 절). 전제가 바뀌었다: 대화 창은 Hermes로 옮겼고 프론트데스크는 관제봇 fallback이며, 승인 푸시는 이미 `/approve <id>`를 보여 준다. 지을 것은 남지 않았다. |
| [AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR_V0.1.md](AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | Part 1(OBSERVATION/LIVE 티어)은 2026-08-09 구현(#648, `crypto/live_tier.py`). Part 2(관찰 티어 자동 설치)는 하지 않기로 결정했다(Thomas 2026-10-06, 아래 결정 절). 지을 것은 남지 않았다. |
| [CONTROL_LANE_SEPARATION_V0.1.md](CONTROL_LANE_SEPARATION_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | K2′ 결정·구현(2026-07-29, `operator.peek_for_halt`). K1 채택·구현: `/kill`·`/pause` 응답과 문서가 실행 중인 작업은 끊기지 않고 끝까지 돈다고 말한다. K3 종결, K4 사지 않음, K5 소멸(Thomas 2026-10-06, 아래 결정 절). |
| [CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md](CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | D1–D3 결정·구현(2026-07-25). D4(standing grant, `approval.v0.3`)는 "필요가 확인되기 전에는 하지 않는다"로 닫았고 D5는 함께 소멸했다(Thomas 2026-10-06, 아래 결정 절). 지을 것은 남지 않았다. |
| [COST_GATE_RESET_THE_RECORD_V0.1.md](COST_GATE_RESET_THE_RECORD_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | §6-2 추인(2026-09-26), §6-1 그대로 둠(Thomas 2026-10-06). §6-3 측정을 했다: 비용 게이트가 막은 22건은 모두 손절(가격 기준 −1R)이었고, 손절 폭 22–62 bps의 진입이다. `MAX_ENTRY_COST_R`을 완화할 근거는 없다(아래 "측정" 절). |
| [SYSTEM_SCORECARD_V0.1.md](SYSTEM_SCORECARD_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | Q1–Q6 모두 결정(Thomas). Q1: 코어 백업 age 암호화 가동(#1024, 2026-10-06; 첫 키 노출로 같은 날 키 교체). Q2 (a): 체인 `google_ai_studio,openrouter,groq`. Q3: §3 처리안 권고대로. Q4 (a): D8 범위에서 `general.specialist` 제외. Q5: 에포크 경계는 cohort 마감마다, 첫 경계 2027-03-22. Q6 (c): D5는 첫 cohort 판정 때 묻는다. |
| [TEMPLATE_RSI_DRAW_MASS_V0.1.md](TEMPLATE_RSI_DRAW_MASS_V0.1.md) | `IMPLEMENTED` | 2026-10-06 | 선택지 0(현상 유지)으로 결정했다(Thomas 2026-10-06, 아래 결정 절). §5 재독은 D3 종료(1차 cohort 마감 2027-03-22) 때 하고, 그 전에는 B(family 재배분)를 D3가 막는다. 지을 것은 남지 않았다. |

## 대체됨 (1)

후속 문서가 대신한다.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [LIVE_OUTCOME_CORRECTION_RECORD_V0.1.md](LIVE_OUTCOME_CORRECTION_RECORD_V0.1.md) | `SUPERSEDED` | 2026-08-23 | `LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md`가 대신한다. 이 설계는 구현된 적 없다. |

## 측정 기록 (5)

결정할 것이 없는 관측 기록.

| 제안서 | 상태 | 날짜 | 요약 |
|---|---|---|---|
| [EQUITY_PERP_S1_MEASUREMENTS_V0.1.md](EQUITY_PERP_S1_MEASUREMENTS_V0.1.md) | `RECORD` | 2026-08-05 | 2026-08-04~05 측정 기록. 설계 제안이 아니라 **관측값**이며, 재현 방법을 함께 적는다. |
| [DIP_WATCH_D_RULE_V0.1.md](DIP_WATCH_D_RULE_V0.1.md) | `RECORD` | 2026-09-23 | 반박된 아이디어의 측정 기록. 효과가 남는 입력은 BitMEX XBTUSD뿐이었고 BitMEX는 2026-09-22에 거래를 끝냈다. 대체 후보 3곳(Coinbase, 바이낸스 무기한, Deribit)은 같은 보수 조건에서 효과가 없다. 결정할 것은 없다. |
| [FORWARD_VERDICT_REGIME_EPISODES_V0.1.md](FORWARD_VERDICT_REGIME_EPISODES_V0.1.md) | `RECORD` | 2026-09-27 | 측정 기록. forward 판정 6건이 BTC 일봉 regime 구간 3–5개(하락 추세 0일)에서 나왔고, 반박·성숙 판정은 시간 분산 검사 없이 나온다. 에포크 경계에서 볼 결정 항목 2개(§5)를 함께 적는다. |
| [SYSTEM_SCORECARD_V0.2.md](SYSTEM_SCORECARD_V0.2.md) | `RECORD` | 2026-10-06 | v0.1(2026-10-05, 6/10)의 질문 여섯 개가 모두 처리된 뒤 같은 기준으로 다시 잰 점수다. 종합 7/10. 결정할 것은 없고, 다음 점수를 움직일 지점을 적는다. |
| [SYSTEM_SCORECARD_V0.3.md](SYSTEM_SCORECARD_V0.3.md) | `RECORD` | 2026-10-06 | v0.2(같은 날 05:30Z, 7/10) 뒤 같은 기준으로 다시 잰 점수다. 종합 7/10 그대로다(평균 7.0 → 7.1, 추기 2에서 7.3). 개발 프로세스가 7에서 8로, 추기 2에서 9로 올랐다. 결정할 것은 없다. |
