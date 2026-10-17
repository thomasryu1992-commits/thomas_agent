# 제안: Thomas Agent 목표 아키텍처 — 7개 계층의 지도와 증거 관문 로드맵 (DRAFT v0.1)

**상태:** DRAFT 2026-10-11 — **2026-10-17 사실 정정:** C7 PR-D #1216과 PR-A #1231이 병합됐다(배포 없음). C7의 남은 구현은 PR-B·PR-C다. H6는 H6a·H6b·H6d-min이 지어졌고 H6b-shadow·H6c가 남았다. 최종 목표(승인된 범위 안의 자율 투자 운영)와 지금의 운영 제한을 한 장에 묶은 지도다. 새 실행 권한이나 정책 권위를 만들지 않으며, 각 줄의 권위는 줄마다 적은 소유 문서에 있다. Thomas의 검토를 기다린다. 결정이 필요한 것은 여섯 가지다: 포트폴리오 전체 위험 한도가 주문을 막는지(D-A1), 실행 단계를 자산군별로 어떻게 적용하는지(D-A2), LIVE_SCALED 진입 규칙(D-A3), 크립토 현물 주문을 어느 단계에서 다루는지(D-A4), C7 D2(D-A5, **우선 해결 대상** — C7 PR-B 설계보다 먼저), IV–RV 측정을 지을지(D-A6). 전역 LOCAL_ONLY Guard의 갱신 완료는 이 문서 기준으로 미확인(PENDING)이다.

**기준:** origin/main `71226ab7` (#1212), 2026-10-11. 운영 상태는 읽기 전용 명령으로만 확인했다: `live_readiness`, `register_execution_stage --show`, `holdings_board` 기본 렌더.
**입력:** Thomas의 7계층 구상과 다섯 가지 답(2026-10-11, 모두 기본값 채택).
**이 문서의 자리:** 지도다. 결정은 줄마다 이름을 적은 소유 문서에 있다. 이 문서와 소유 문서가 다르면 소유 문서가 맞다.

---

## 0. 한 줄

- **목표:** 인간은 투자 정책과 위험 허용 범위를 정한다. AI는 그 안에서 일상적인 투자 운영을 스스로 한다.
- **현재:** 크립토 선물에서는 그 운영 장치가 이미 지어졌고 2026-08에 실제 주문을 냈다. 지금 멈춰 있는 이유는 **장치가 아니라 증거**다. 검증된 엣지를 가진 전략이 없다.
- **순서:** 회계(①)가 먼저 권위를 갖추고, 크립토 전략이 증거를 낸 뒤에 다른 자산군을 연다.

---

## 1. 최종 목표

### 1.1 목표 문장 (Thomas 2026-10-11)

> Thomas가 사전에 승인한 전략, 자산군, 위험 한도 및 실행 권한 범위 안에서 AI가 투자 판단, 주문 실행, 포지션 관리,
> 리밸런싱, 성과 분석까지 자동 수행하는 시스템. 궁극적으로 매번 개별 주문을 승인하지 않아도 운영될 수 있어야 한다.

> 인간은 투자 정책과 위험 허용 범위를 결정하고, AI는 검증된 정책 안에서 일상적인 투자 운영을 자동으로 수행한다.

### 1.2 계속 Thomas의 명시적 승인이 필요한 것

자동화 수준이 높아져도 아래는 AI가 스스로 하지 않는다. AI는 제안할 수 있지만, 검증과 승인 없이 운영에 반영하지 않는다.

1. 새 전략의 실거래 승격
2. 새 자산군의 거래 활성화
3. 거래소·증권사 추가
4. 위험 한도 상향
5. 레버리지 한도 확대
6. 실행 단계 승격
7. 운영 정책의 중대한 변경
8. 자동 리밸런싱 권한의 최초 활성화 또는 확대

이상이 생기면 fail-closed가 우선이다. 성과 분석은 개선 후보만 만든다. 전략 승격이나 운영 설정 변경으로 바로 이어지지 않는다.

### 1.3 장기 목표 자산군

이 목록은 **장기 범위**다. 거래 권한이나 구현 확정을 뜻하지 않는다. 현재 상태는 §5에 있다.

- 주식 현물
- 암호화폐 현물
- 암호화폐 선물
- 주식 관련 선물, 그리고 기존 Equity Perp(HIP-3) 검토 범위
- 주식 옵션
- 암호화폐 옵션
- 현금과 기타 포트폴리오 자산(채권·금 ETF 등)

옵션은 반드시 채택하는 자산군이 아니다. 검증된 대안보다 위험 대비 효율이 좋을 때만 도입하는 **조건부 후보**다
(`MULTI_ASSET_EXPANSION_V0.1.md` D2). 국내 선물·옵션은 규제·계약·API 기록 전까지 실행 범위 밖이다. 영구 제외는 아니다.

### 1.4 구현 우선순위 (Thomas 2026-10-11, 기존 결정 순서)

1. 통합 자산관리와 정확한 회계
2. 기존 크립토 거래 시스템의 증거 검증
3. 크립토의 승인된 실행 단계 진입
4. P3 조건 충족 후 주식 거래 확장
5. P4 조건 충족 후 옵션 도입 검토
6. 별도 승인된 자동 리밸런싱

---

## 2. 지금 서 있는 곳 (2026-10-11)

| 항목 | 값 | 출처 |
|---|---|---|
| 실행 단계 | **PAPER** (`stage_03b4e2af3cf13775fbbd`, 정책 1.6.2) | `register_execution_stage --show` |
| 실거래 진입 | **불가.** 막는 것: 실행 단계 PAPER, live 게이트 닫힘(`MVP_LIVE_TRADING` 미설정), 무장된 전략 0개(15개 중), 거래소 계약 확인 오래됨 | `crypto.live_readiness` |
| live 게이트 | 2026-10-07 H0 리뷰 뒤 닫힘. 거래 키를 유지보수 평면에서 제거(H1-a), 주문 어댑터가 송신 직전에 실행 단계를 확인(R2) | `docs/history/2026-10-07-egress-stage-check.md`, `2026-10-07-maint-no-trading-credential.md` |
| 등록된 실거래 예산 | 주문 ≤200, 하루 25건, 미결제 ≤300, 손실 ≤50 USDT, 2027-08-30까지 유효 | `crypto.live_readiness` |
| 실주문 가능한 거래소 | `live_trading_budget` 스키마의 `venue` enum = `["binance_futures"]`. 다른 거래소의 실주문은 스키마 변경(거버넌스) 전에는 구조적으로 불가능하다 | `schemas/live_trading_budget.v0.1.schema.json` |
| 전략 증거 | forward cohort 115개 중 확정 0, 반박 5 (2026-10-01 기준). LIVE 승격에는 FORWARD_CONFIRMED가 필수다 | `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md` §1, `SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md` D1 |
| 연구 일시 중지 | cohort 1 마감 **2027-03-22**까지 `crypto/`에 새 템플릿·가설·시험 금지(예외 목록 있음) | `CLAUDE.md`, `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md` |
| 통합 보드 | 범위 v1(토스, 바이낸스 선물·현물·Simple Earn)의 NAV가 완전하다. 낙폭은 알림만(-20%), 목표 대비 이탈은 표시만 | `holdings_board`, `MULTI_ASSET_EXPANSION_V0.1.md` P2, `TOTAL_ASSET_ALLOCATION_V0.1.md` Q9 |
| H6 현금흐름 | shadow 원장 운영 중. 판정에는 쓰지 않는다. H6b-shadow, H6c 모두 미승인 | `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` |
| C7 | PR-0 #1210, PR-D #1216, PR-A #1231 병합(2026-10-17). **운영 배포 없음**(PR-D HOLD, PR-A는 PR-B 전 배포 금지). 남은 것: PR-B(D2 뒤), PR-C. D2 부분 결정 | `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12, §12.4 |
| 병합된 것 | #1211(결정 기록), #1212(Toss 연속성 진단, 터미널 전용). #1212는 **실제 원장에서 아직 실행하지 않았다** | git |
| 전역 LOCAL_ONLY Guard | **PENDING — 이 문서 기준으로는 미확인.** #1212 본문은 `GLOBAL_GUARD_PENDING`이고, 저장소에는 갱신과 다섯 항목 검증의 완료 기록이 없다. 확인이 필요한 사실로 남긴다 | #1212 본문 |

---

## 3. 7개 계층 지도

열의 뜻은 이렇다. **지어진 것**은 main에 있는 코드·문서다. **안 지어진 것**은 목표에 필요하지만 없는 것이다. **지금 막는 것**은 다음 단계를 여는 관문이다.

### ① Portfolio Intelligence — 전체 자산 파악

| | 내용 |
|---|---|
| 소유 | `MULTI_ASSET_EXPANSION_V0.1.md`(P1·P2), `TOTAL_ASSET_ALLOCATION_V0.1.md`, `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`(H6), `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`(C7) |
| 지어진 것 | `holdings/`: 계좌 읽기(`toss_account`, `binance_wallet`), 합산·낙폭 알림(`combined`), 목표 대비 이탈 표시(`allocation`), 외부 경계(`disclosure`), 분류(`classification`), 기준선 로그(`baseline_log`), 현금흐름 shadow 원장과 readiness(`cash_flows`), Toss 연속성 진단(#1212). 크립토 선물 계정은 `crypto/account.py`가 따로 읽는다 |
| 안 지어진 것 | unit 회계(입출금과 성과 분리, H6b-shadow·H6c). C7 이력 판정·채택(PR-B·PR-C. 체크포인트 감지 PR-A는 지어짐, 배포 없음). 계좌별·전체 수익률. 주식 포지션의 원가·손익 회계. 옵션 포지션 표현 |
| 지금 막는 것 | H6b_shadow_ready(7일·7일자, Toss 현금 의미, 예외 0건). **C7 D2(D-A5)**: C7 PR-B의 재수집·이력 판정 설계보다 먼저 정해야 한다. **C7 구현**(PR-D → A → B → C, H6c의 선행 조건, #1211 Q3. 2026-10-17 기준 PR-D·PR-A 병합, B·C 남음). 순서는 §6 G1a–G1d |

### ② Market Intelligence — 시장 분석

| | 내용 |
|---|---|
| 소유 | `REMAINING_WORK.md` §C·§H, `EQUITY_PERP_LANE_V0.1.md` |
| 지어진 것 | `crypto/market_data.py`(Binance 선물, Hyperliquid 수집기 — S1, 2026-08-04부터 운영), `features`, `indicators`, `candle_archive`, `oi_store`, `orderbook_store`, `positioning_store`, `data_review` |
| 안 지어진 것 | 주식 데이터 계층(장 시간·휴장일·호가 단위·기업행동). 옵션 데이터. IV–RV 측정(연구 일시 중지의 예외로 허용됐지만 **짓지 않았다**. 짓는 것은 별도 승인, D-A6). 외부 계획이 말하는 "ResearchSignal v2"(첫 cohort 판정 뒤에 다시 연다, `PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md`) |
| 지금 막는 것 | 연구 일시 중지(2027-03-22). 주식은 P3, 옵션은 P4 |

### ③ Strategy Engine — 전략 결정

| | 내용 |
|---|---|
| 소유 | `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`, `SELECTION_MULTIPLICITY_AND_HOLDOUT_REUSE_V0.1.md`, `RESEARCH_EPOCH_V0.1.md`, `REMAINING_WORK.md` §I·§L |
| 지어진 것 | `crypto/`: 전략·템플릿(`strategy`, `template_space`, `generator`), 생산(`factory`), 후보 제안(`proposer`, `candidate_ranking`), forward 검증(`forward_cohort`, `forward_confirmation`, `null_control`), 승격·퇴출(`promotion`, `live_promotion`, `retirement`). **AI가 제안하고 Thomas가 승격한다**: `scripts/promote_strategy_candidates.py --live-tier LIVE` |
| 안 지어진 것 | 주식·옵션 전략. 헤지·현물 비중 조절 전략. 리밸런싱 전략(⑥) |
| 지금 막는 것 | 검증된 엣지 없음(FORWARD_CONFIRMED 0건). 연구 일시 중지. 판정 규칙은 연구 에포크 경계(2027-03-22부터 cohort 마감마다)에서만 완화 |

### ④ Risk Management — 거래 허용 판단

| | 내용 |
|---|---|
| 소유 | `docs/runtime-contracts/RUNTIME_SAFETY_INVARIANTS_V0.1.md`, `EXECUTION_STAGE_V0.1.md`, `MULTI_ASSET_EXPANSION_V0.1.md` P2 |
| 지어진 것 | 주문 전 관문(`crypto/pre_order_gate`), 위험 한도(`risk_limits` — 미등록 시 기본값: 하루 -2R, 주간 -5R, 연속 3회, 낙폭 -10%), 실거래 예산(`live_budget`, `live_allowance`), 차단기(`breaker_watch`, `cooldown`, `guards`), env 게이트(`safety_gate`), 준비도(`live_readiness`). 포트폴리오 단위로는 낙폭 -20% **알림만**(`holdings/combined`) |
| 안 지어진 것 | 여러 자산군을 묶는 위험 한도가 주문을 막는 장치. 옵션 Greeks·만기 위험. 여러 거래소에 걸친 청산 위험 |
| 지금 막는 것 | **D-A1**(포트폴리오 한도가 주문을 막는가). 지금 결정은 반대 방향이다: P2는 알림만이고, -20%는 거래 중단 조건이 아니다(2026-10-11) |

### ⑤ Execution Engine — 주문 실행

| | 내용 |
|---|---|
| 소유 | `EXECUTION_STAGE_V0.1.md`, `RUNTIME_SAFETY_INVARIANTS_V0.1.md`, `REMAINING_WORK.md` §C |
| 지어진 것 | 바이낸스 USDⓈ-M 선물 한 곳: 주문 어댑터 선택(`crypto/live_execution.select_order_adapter`, `BinanceFuturesOrderAdapter`), 경로(`live_route`), 주문(`live_order`, `order_request`), 대사(`live_reconcile`, `live_settlement`), 거래소 계약 확인(`venue_contract`), 테스트넷(`testnet_execution`), 단계 원장(`execution_stage`, `scripts/register_execution_stage.py`) |
| 안 지어진 것 | 크립토 현물 주문. 주식 브로커 어댑터(토스 주문 API는 공식 스펙에 있지만 읽기 키가 곧 주문 키이고, 샌드박스가 없고, 토큰이 클라이언트당 1개다 — `PORTFOLIO_AND_STOCK_TRADING_PLAN_AUDIT_V0.1.md` §D). 옵션 다중 레그 주문 |
| 지금 막는 것 | 실행 단계, live 게이트, 무장 0개(§4). 새 거래소는 `venue` enum과 규제 기록이 먼저다 |

### ⑥ Portfolio Rebalancing — 자산 재배분

| | 내용 |
|---|---|
| 소유 | `TOTAL_ASSET_ALLOCATION_V0.1.md` Q9·§11.1(2026-10-07) |
| 지어진 것 | 목표 배분(주식 40·국고채 37·금 13·BTC 2.5·현금 7.5)과 이탈 **표시**(`holdings/allocation`) |
| 안 지어진 것 | 리밸런싱 전략, 비용·세금 반영, 유동성·주문 가능성 검증, 자동 주문, 체결·잔고 대사, 이상 시 중단 |
| 지금 막는 것 | 2026-10-07 결정(표시만, 비중 한도 없음). H6c 뒤에 **다시 결정하기로 예정돼 있다**(§6 G6). H6c가 끝나도 자동 리밸런싱이 승인되는 것은 아니다 |

### ⑦ Feedback Loop — 성과 개선

| | 내용 |
|---|---|
| 소유 | `RESEARCH_EPOCH_V0.1.md`, `CRYPTO_STRATEGY_EDGE_ORDER_V0.1.md`, `REMAINING_WORK.md` §L |
| 지어진 것 | `crypto/feedback`, `live_evidence`, `counterfactual`, `robustness`, `independence`, forward cohort 판정과 null control. 개선은 후보(`proposer`)로만 나오고, 운영 반영은 승인 문을 지난다 |
| 안 지어진 것 | 자산군을 묶는 성과 귀속(전략별·자산군별 기여) |
| 지금 막는 것 | 판정 규칙의 완화는 연구 에포크 경계에서만 한다. 첫 경계는 2027-03-22다 |

---

## 4. 실행 단계와 "자율"의 뜻

**사다리 (확정, `EXECUTION_STAGE_V0.1.md`):** `READ_ONLY → SHADOW → PAPER → SIGNED_TESTNET → LIVE_AUTONOMOUS → LIVE_SCALED`

| 단계 | 장치 | 지금 |
|---|---|---|
| READ_ONLY, SHADOW, PAPER | 거래소 노출 없음 | **PAPER** |
| SIGNED_TESTNET | 테스트넷 서명 주문(`run_signed_testnet_cycle.py`) | 지어짐 |
| LIVE_AUTONOMOUS | 자율 레그, 슬리피지 probe, 전략 LIVE 무장. **"사전 승인한 전략과 한도 안에서 스스로 주문"이 바로 이 단계다** | 이 단계가 여는 장치는 **지어졌다**. 사다리 이전인 2026-08에 실제 주문을 냈다(첫 live 캠페인, 08-15~08-18) |
| LIVE_SCALED | LIVE_AUTONOMOUS와 같은 문. **진입 규칙은 별도 결정**(D-A3) | 미정 |

- **장치와 증거를 나눠 본다.** 크립토 선물의 자율 주문 장치(자율 레그, 브래킷, 대사, 차단기)는 이미 있고, 2026-08에 실제 주문을 냈다. 사다리는 2026-09-15에 생겼고, 이 기계는 그때 **PAPER**로 시작했다(`EXECUTION_STAGE_V0.1.md` decision 1). 그 뒤 단계 원장의 이력은 이 문서에서 읽지 않았다. 지금 실거래가 0인 이유는 세 가지다: 단계가 PAPER이고, 게이트가 닫혀 있고, 무장된 전략이 0개다. 무장에는 FORWARD_CONFIRMED가 필요하고, 그런 전략이 아직 없다.
- **카나리 rung은 없다.** 2026-09-15에 결정했고, 09-29, 10-05, 10-11 세 번 다시 확인했다. 새 rung을 만들거나 승격 조건을 완화하지 않는다.
- **닫는 쪽은 단계가 막지 않는다.** reduceOnly 청산과 보호 재확인은 단계와 무관하다(`EXECUTION_STAGE_V0.1.md`).
- **이름이 같은 다른 체계와 섞지 않는다.** 외부 계획들이 쓰는 "Crypto_AI_System", "Phase 7.x", "Stage 0–7", "LIVE_CANARY"는 이 저장소의 체계가 아니다. 이 저장소에는 Phase 번호 체계가 없다(`PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md`). 이 문서의 단계 이름은 위 사다리뿐이다.
- **사다리를 자산군별로 어떻게 쓸지는 정해지지 않았다.** 지금 단계 원장은 크립토 레인의 실행 권한이다. 한편 #1211 Q2는 "크립토의 승인·실행 권한을 주식에 상속하지 않는다"고 정했다. 새 rung을 만들지 않으면서 자산군마다 단계를 따로 두는 방법은 P3에서 정한다(D-A2).

---

## 5. 자산군별 로드맵

| 자산군 | 지금 조회 | 지금 주문 | 다음 관문 | 진입 증거 | 승인 |
|---|---|---|---|---|---|
| **암호화폐 선물** (바이낸스 USDⓈ-M) | 예(`crypto/account`, 보드 범위 v1) | 장치 있음, **PAPER·게이트 닫힘·무장 0** | 첫 실거래(G2a 전략 승격 → G2b 실행 단계 → G2c 주문 실행) | 세 관문이 각각 다르다. 판정은 연구 에포크 규칙대로(§6) | Thomas: 관문마다 따로 |
| **암호화폐 현물** (바이낸스 현물·Simple Earn) | 예(보드 범위 v1, 읽기 전용 키) | **없음.** 주문 어댑터 없음, `venue` enum 밖 | 어느 단계에서 다룰지 미정(D-A4) | 규제 판단(기존 크립토 판단의 연장인지), 어댑터, 스키마 변경 | Thomas |
| **주식 현물** (토스증권) | 예(P1, 부록 B: 조회만) | **없음.** 부록 B는 조회만 덮는다 | **P3**(G4) | 2027-03-22 이후, 크립토 FORWARD_CONFIRMED ≥1, 주문용 새 규제 판단, `venue` enum 확장, 세 이음매로 들어오되 주식 특성은 따로 검증 | Thomas(P3 진입은 별도 승인) |
| **주식 관련 선물 / Equity Perp** (Hyperliquid HIP-3) | 시세만(S1 수집기, 2026-08-04~) | **없음.** 실주문은 S0 확정 승격 전 불가 | `REMAINING_WORK.md` §H의 S2 이후 순서, P3 | S0 확정(지금 강도 잠정), 측정 기록 | Thomas |
| **주식 옵션 / 암호화폐 옵션** | 없음 | 없음 | **P4, 마지막**(G5) | P3 + 다중 레그 표현 + 옵션 판정 규칙(에포크 경계) + 거래소 S0 + D2 비교 기준 비준. **조건부 후보**다 | Thomas |
| **국내 선물·옵션** | 없음 | 없음 | 실행 범위 밖(영구 제외 아님) | 규제·계약·API 기록 | Thomas |
| **현금과 기타 자산** (원화·달러 현금, 채권·금 ETF는 토스 경유) | 예(토스 매수가능금액. `cashBuyingPower`의 의미는 미검증) | 없음(리밸런싱은 ⑥) | ⑥ 재결정(G6) | H6c 권위 + 별도 결정 | Thomas |

---

## 6. 증거 관문

날짜는 이미 정해진 것만 쓴다. 이 문서는 개발 완료일을 정하지 않는다.

| 관문 | 여는 것 | 진입 증거 | 승인 | 소유 |
|---|---|---|---|---|
| **G1a** H6b-shadow | unit 회계를 옆에서 **표시만** | H6b_shadow_ready(7일·7일자, Toss 현금 의미 PASS, 예외 0건, 스키마). 시스템이 알린다 | Thomas | H6 §7 |
| **G1b** C7 D2 증거와 결정 (**D-A5, 우선**) | C7 PR-B의 재수집·이력 판정 설계 방향 | D2를 고르려면 증거가 둘 필요하다. 각각 별도 승인이다: 실제 원장에 대한 Toss 연속성 진단(#1212), 바이낸스 과거 창 probe(C7 §12.3의 7). A안을 고를 수 있는 시간 제약은 약 2027-04-01. **D2는 C7 전체 구현 뒤가 아니라 PR-B 앞에 정한다** | Thomas | C7 §12.1–§12.3 |
| **G1c** C7 구현 | PR-D → PR-A → PR-B → PR-C, 이 순서와 의존관계 그대로. PR-A는 감지만, 쓰기 차단은 PR-C에서만 | PR마다 구현, 머지, 배포를 따로 승인. PR-B는 G1b(D2)가 정해진 뒤에 설계한다 | Thomas, PR마다 | C7 §10·§12.1 D1 |
| **G1d** H6c 활성화 | unit 회계가 낙폭 판정의 권위가 된다 | G1a 운영 + G1c 배포(C7이 H6c의 선행 조건, #1211 Q3) + Toss 현금 의미 + 미해결 예외 0. **H6c 활성화 승인은 D2 결정과 다른, 따로 받는 승인이다** | Thomas | H6 §7, #1211 Q3 |
| **G2a** 전략 승격 | 전략 1개를 LIVE 등급으로 무장 | **FORWARD_CONFIRMED ≥1이 필요 조건**이다(`SELECTION_MULTIPLICITY…` D1). 첫 확정이 2027-03-22 전에 나와도 일시 중지는 끝나지 않고, E1·E2가 발동한다(§L) | Thomas: 승격 문(`promote_strategy_candidates.py --live-tier LIVE`) | 같은 문서, `REMAINING_WORK.md` §L |
| **G2b** 실행 단계 승격 | PAPER → SIGNED_TESTNET → LIVE_AUTONOMOUS | 한 번에 한 칸(CLIMB), 단계마다 Thomas 승인이 증인이다. **LIVE_AUTONOMOUS로 오르려면 완결된 signed testnet cycle 하나를 지정해야 한다**(`--testnet-cycle`, decision 2). 완결의 뜻: 진입 대사 → 보호 주문 RESTING 확인 → 철회 → reduceOnly 청산 대사 → 거래소 포지션 깨끗(decision 11) | Thomas, 칸마다 | `EXECUTION_STAGE_V0.1.md` |
| **G2c** 실제 주문 실행 | 실주문 송신 | G2a(무장 ≥1)와 G2b(LIVE_AUTONOMOUS) 둘 다, 그리고 live 게이트 재개(`MVP_LIVE_TRADING=real`, 확인 문구, 포지션 확인 먼저), 등록된 예산과 위험 한도, 거래소 계약 확인 최신, 주문마다 주문 전 관문 통과. 셋 중 하나라도 없으면 실주문은 없다 | Thomas: 게이트 재개 | `RUNTIME_SAFETY_INVARIANTS_V0.1.md`, `live_readiness` |
| **G3** LIVE_SCALED | 규모 확대 | **진입 규칙 미정**(D-A3) | Thomas | `EXECUTION_STAGE_V0.1.md` |
| **G4** P3 주식 | 주식 연구, 아키텍처, paper, 브로커 검증, 주문 승인, 제한적 실거래 — 여섯 단계가 각각 따로 승인된다 | **2027-03-22 이후** + 크립토 FORWARD_CONFIRMED ≥1 + 주문용 규제 판단 + `venue` enum 확장 + D-A2 | Thomas, 단계마다 | `MULTI_ASSET_EXPANSION_V0.1.md` §5, #1211 Q1·Q2 |
| **G5** P4 옵션 | 옵션 도입 **검토** | P3 + 다중 레그 + 옵션 판정 규칙 + 거래소 S0 + D2 비교 기준 비준 | Thomas | 같은 문서 §5·§6 D2 |
| **G6** 자동 리밸런싱 | 리밸런싱 주문 | G1d(H6c) 완료 뒤 **2026-10-07 결정을 다시 여는 별도 결정**. H6c 완료가 곧 승인은 아니다 | Thomas | `TOTAL_ASSET_ALLOCATION_V0.1.md` |
| **G7** 여러 자산군 실거래 | 두 번째 자산군의 실주문 | D-A1(포트폴리오 한도), D-A2(자산군별 단계) | Thomas | 이 문서 §7 |
| (참고) 엔진 비중 첫 증액 | 크립토 엔진 배분 | cohort 1 판정(**2027-03-22**) | Thomas | `TOTAL_ASSET_ALLOCATION_V0.1.md` Q5 |

**날짜 두 개:**
- **2027-03-22**는 cohort 1 마감이자 첫 연구 에포크 경계이고, P3를 가장 빨리 다시 검토할 수 있는 날이다.
- **약 2027-04-01**은 C7 D2 A안에서 바이낸스 이체 이력을 다시 읽을 수 있는 시간 제약이다(`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.3). **자동매매의 개발 완료일이나 활성화 목표일이 아니다.**

---

## 7. 기존 결정과의 관계

### 7.1 DECISION_REQUIRED

| ID | 질문 | 왜 열려 있나 | 언제까지 정해야 하나 |
|---|---|---|---|
| **D-A1** | 포트폴리오 전체 위험 한도가 **주문을 막는가** | 구상의 ④는 총자산 위험 한도를 주문 관문으로 둔다. 지금 결정은 P2 알림만이고, -20%는 거래 중단 조건이 아니다(2026-10-07, 10-11). 막게 하려면 P2를 뒤집어야 한다 | 두 번째 자산군이 실거래를 시작하기 전(G7) |
| **D-A2** | 실행 단계를 자산군별로 어떻게 적용하는가 | 단계 원장은 지금 크립토 레인의 권한이다. #1211 Q2는 크립토 권한의 상속을 금했다. 새 rung을 만들지 않고 자산군별로 단계를 두는 방법이 없다 | P3 진입 전(G4) |
| **D-A3** | LIVE_SCALED 진입 규칙 | 사다리가 "별도 결정"으로 남겨 두었다 | G3 전 |
| **D-A4** | 크립토 현물 주문을 어느 단계에서 다루는가 | 조회는 범위 v1에 있지만 주문 경로가 없다. `MULTI_ASSET_EXPANSION_V0.1.md` §3은 "기존 크립토 판단의 연장인지 확인 필요"로 남겼다 | 제안이 나올 때 |
| **D-A5** (**우선 해결 대상**) | C7 D2(Toss 증명 방식, cutover 유지) | A안 조건부 우선, B안 비추천, C안 대안. 최종은 미승인. 이 문서는 A/C를 정하지 않는다 | **C7 PR-B 설계 전**(G1b). 결정에 필요한 증거 둘은 각각 별도 승인. A안의 시간 제약은 약 2027-04-01 |
| **D-A6** | IV–RV 측정을 지을지 | 연구 일시 중지의 예외로 허용됐다(2026-10-06). 짓는 것은 별도 승인이다 | 선택 사항 |

### 7.2 충돌이 아니라 예정된 순서인 것

- **⑥ 자동 리밸런싱:** 2026-10-07 결정을 유지하고, H6c 뒤에 다시 결정한다(G6). 이 문서에서는 충돌로 보지 않는다.
- **① 계좌별·전체 수익률:** unit 회계는 설계됐고(D-H6-4) 짓지 않았다. H6c는 C7을 기다린다.
- **③·⑦ "AI는 제안, 사람은 승인":** 이미 시스템의 모양이다. 제안(`proposer`) → forward 검증 → 승격 문(Thomas)이고, 판정 규칙은 에포크 경계에서만 바뀐다.
- **크립토 현물:** 조회 범위에 있다는 것과 자동매매 권한은 다르다. 지금은 조회만 한다.

### 7.3 이 문서에서 다시 열지 않은 것

카나리 rung, `stocks/` 패키지(2026-08-03 기각), P3 관문, 연구 일시 중지, 코드 상수 게이트("환경이 게이트다", 2026-08-10).
모두 소유 문서의 결정 그대로다.

---

## 8. 재검토 조건

다음이 바뀌면 이 지도를 고친다.
- 소유 문서의 결정이 바뀔 때
- 첫 FORWARD_CONFIRMED가 나올 때
- 2027-03-22 판정이 나올 때
- H6c가 열릴 때
- 새 거래소·증권사의 규제 기록이 생길 때
- D-A1–D-A6 중 하나가 정해질 때

지도는 따라가는 문서다. 지도가 결정을 앞서지 않는다.

## 9. 이 문서가 바꾸지 않는 것

런타임, 스키마, 정책, 상수, 스케줄, 실행 단계, live 게이트, 예산, 위험 한도, 보드, 원장을 바꾸지 않는다. 새 실행 권한, 새 rung,
새 승격 경로를 만들지 않는다. 구현 순서는 소유 문서가 정한다: `REMAINING_WORK.md`, `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §7,
`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §10, `MULTI_ASSET_EXPANSION_V0.1.md` §5.
