# 제안: "전체 자산관리 검증 + 토스 주식 자동매매" 계획 — 저장소 대조 감사 (v0.1)

**상태:** IMPLEMENTED 2026-10-17 — **2026-10-26 상태 정정:** 마지막으로 열려 있던 D2가 2026-10-17에 C안으로 정해졌다(`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.5). C7 다섯 PR은 같은 날 `candidate-1238`로 운영에 배포됐다(§12.7). 이 문서가 묻던 결정은 모두 정해졌고, H6c는 그 문서의 별도 결정이다. **이전 상태(2026-10-11, PARTIALLY DECIDED):** Q1·Q2·Q4·Q5는 권고대로 결정됐다(Thomas). 주식 자동매매는 P3 관문 뒤에 두고, `stocks/`를 다시 열지 않으며, 카나리 rung의 네 번째 요청과 그 비승인을 기록하고, 문서 6개는 만들지 않는다. Q3: C7은 H6c의 선행 조건이다. D1·D3은 방향 동의, D4는 조건부 동의다. 열린 것: **D2**, 곧 Toss 연속성 증명 방식과 cutover 유지 여부. A안이 조건부 우선 검토안이고 최종 cutover 결정은 미승인이다(`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.2–§12.3). C7 구현은 시작하지 않았다(작성 당시. **2026-10-17 정정:** PR-D #1216·PR-A #1231이 병합됐고 배포는 없다. `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.4). 토스 주문 능력은 공식 스펙 v1.2.24로 기록했다(§D). 코드·스키마·정책 변경 없음.

**대조 기준:** origin/main `c9bcf1af`(#1209), 2026-10-11. 운영 상태는 `holdings_board` 기본 렌더(외부용, 2026-10-11T06:33:50Z
저장본)만 읽었다. LOCAL_ONLY 파일, `--full`/`--local`, 원장 원문은 읽지 않았다.
**입력:** Thomas가 붙여 준 "Thomas Agent — Portfolio Validation & Toss Stock Auto-Trading Roadmap"(이하 "계획"). §번호와 Part 번호는
계획의 것이다.
**선행 문서:** 같은 계열의 외부 계획이 세 번 들어왔다(`docs/history/2026-09-29-no-canary-rung-reaffirmed.md`,
`CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md`, `PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md`). 이번 계획은 크립토가 아니라 자산관리와
주식을 다루지만, 카나리와 코드 상수 요구가 다시 들어 있다.
**표기:** 계획 §28의 태그를 쓴다. **VERIFIED**(코드·테스트·명령·공식 원문으로 확인), **PARTIALLY_VERIFIED**, **NOT_VERIFIED**,
**DESIGN_PROPOSED**(저장소에 설계만 있음), **DECISION_REQUIRED**, **BLOCKED**(기존 결정의 관문 뒤).

---

## 0. 한 줄

1. **계획이 새로 설계하자는 "현금흐름 조정 성과·투자 낙폭"은 H6의 unit 회계다.** D-H6-4로 결정됐고(2026-10-08), 아직 짓지 않았다.
   다음 단계 H6b-shadow가 바로 그것을 옆에서만 계산하는 일이다(§A).
2. **C7은 설계, 재현 테스트, D1–D4 권장안까지 이미 있다.** 빠진 것은 Thomas의 답이다(§B).
3. **토스는 공식 주문 API를 제공한다.** 다만 조회 전용 권한이 없고, 모의투자 환경이 없고, 토큰이 클라이언트당 하나다. 그래서 주문
   프로세스를 따로 두면 보드 토큰과 서로를 무효화한다(§D).
4. **주식 자동매매는 P3이고, P3은 2027-03-22 이후다.** 계획의 별도 `Stock_AI_System`은 2026-08-03에 기각된 `stocks/` 형제
   패키지와 같은 모양이다. "Stage 5 Limited Live Canary"는 네 번째로 들어온 카나리 rung이다(§E).

---

## A. NAV와 낙폭 (계획 Part 1, §1–3)

### A.1 지금의 산식 — VERIFIED

- 보드의 낙폭은 **범위 v1 합계 NAV의 고점 대비**다. 입출금도 낙폭으로 읽힌다. 이것은 결함이 아니라 P2 결정(2026-10-07)의 설계이고,
  보드가 매 렌더에 그렇게 적는다("deposits/withdrawals read as drawdown").
- 입출금이 생기면 사람이 `--reset-peak --reason`을 실행한다. 재설정은 H5b 체인 로그에 남는다
  (`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` 머리말, `tests/test_mvp_runtime_holdings_baseline_log.py`).
- 해외 자산은 토스 매매기준율 하나로 환산한다. 환율을 못 읽으면 해외 쪽은 추정하지 않고 비운다
  (`test_an_unreadable_rate_leaves_the_overseas_side_absent_not_guessed`). 그 경우 판정도 보류한다
  (`test_a_usd_holding_without_a_rate_withholds_the_verdict`).
- 같은 자산의 이중 집계: 범위 안 선물↔현물 이체가 출처 창 안에 있으면 coherence가 FAIL이고 NAV를 보류한다
  (`test_a_transfer_in_the_window_withholds_the_nav_through_combine`).

### A.2 합성 시나리오 — 계획 §2의 A–H

시작 NAV 100, 고점 100이다. raw 낙폭은 오늘의 산식이고, unit 낙폭은 D-H6-4 설계를 따른다(입금은 unit 발행, 출금은 소각, 낙폭은
NAV/unit 고점 대비). 아래 값은 저장소 코드가 아니라 설계 산식을 따로 계산한 것이다. **unit 회계 코드는 아직 없다**
(`test_nothing_is_applied_and_no_unit_exists`).

| 시나리오 | NAV | raw 낙폭 | unit 낙폭 | 해석 |
|---|---|---|---|---|
| A 가격 불변, 출금 30 | 70 | **−30%** (알림) | 0% | 출금이 손실로 보인다 |
| B 가격 불변, 입금 30 | 130 | 0% | 0% | 고점이 130으로 오른다 |
| B′ 가격 −10% 뒤 입금 30 | 120 | **0%** | −10% | **입금이 실제 손실을 가린다** |
| C 가격 −10%와 출금 15 | 75 | −25% | −10% | 출금이 손실을 부풀린다 |
| D 내부 이체만 | 100 | 0% | 0% | 창 안이면 coherence FAIL로 판정 보류 |
| E 수수료 0.1 | 99.9 | −0.1% | −0.1% | 비용은 성과다(unit 불변) |
| F 고점 재설정 직후 출금 15 | 85 | −15% | 0% | 재설정을 한 번 더 해야 한다 |
| G 결제 전후 현금 | — | — | — | `toss_cash_semantics` WAITING, 관측 전이다 |
| H 환율 −5% (전부 USD 자산) | 95 | −5% | −5% | 환율은 성과다 |

결론은 다음과 같다.
- 계획이 우려한 두 가지는 실제로 일어난다. 출금은 손실로 보이고(A·C·F), 입금은 손실을 가린다(B′).
- 이를 고치는 산식은 이미 결정돼 있다(D-H6-4). 새로 설계할 지표는 없다.
- 계획의 "Investment Drawdown"은 NAV/unit 낙폭이고, "Raw NAV Drawdown"은 오늘의 낙폭이다. H6c 전까지 둘은 따로 있고,
  판정은 raw가 한다(H6 §7의 3·4단계).
- TWR/MWR: NAV/unit은 시간가중(TWR)에 해당한다. 오차는 "흐름 금액 × 한 시간 수익률"이다(H6 §4). MWR(XIRR)은 설계에 없다.
  필요하다는 증거가 없어 추가를 권하지 않는다.
- **이 시나리오를 저장소 테스트로 고정하는 일은 H6b-shadow의 수용 기준이다**(H6 §7: "합성 시나리오와 shadow 기간의 실제 흐름으로
  확인한다"). 그래서 지금 따로 짓지 않고 H6b-shadow PR에 넣는다.

### A.3 오늘의 −18.8% — NOT_VERIFIED

외부용 렌더(06:33Z)의 값은 다음과 같다.
- NAV 4,547,504 KRW, 고점 5,602,141 KRW(10-10 재설정), 낙폭 −18.8%, 한도 −20%.
- shadow 원장에는 외부 흐름 2건이 있다.

이 낙폭이 시세 하락인지 입출금인지는 판정하지 않았다. 흐름의 방향과 금액은 LOCAL_ONLY이고, 확인은 Thomas의 몫이다. 입출금이었다면 A·F와
같은 경우이고, 처리 방법은 지금 규칙대로 `--reset-peak --reason`이다.

---

## B. C7 복원 안전성 (계획 Part 2, §4–5)

설계(`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`)와 재현 테스트(`tests/test_mvp_runtime_holdings_c7_partial_restore.py`, #1209)가 이미 있다.
계획의 10개 상황을 테스트에 대응시켰다.

| 계획 §4 | 테스트 | 오늘 동작 |
|---|---|---|
| 1 원장 최신, 상태 이전 | `test_c7_3_…` | 복구 — VERIFIED |
| 2 상태 최신, 원장 이전 | `test_c7_4_…` (겹침 안), `test_defect_c7_4_…` (겹침 밖) | 겹침 밖이면 **유실하고 PASS** — 결함 고정 |
| 3 append 직후 종료 | `test_c7_2_…` | 복구 — VERIFIED |
| 4 상태 저장 직후 종료 | `test_c7_7_…` (커서가 앞선 경우) | 조용한 날로 읽음 — PARTIALLY_VERIFIED |
| 5 백업 중 새 이벤트 | 설계 §11(상태 잠금 아래 두 파일 복사) | DESIGN_PROPOSED. bind mount flock 공유는 **미확인** |
| 6 시점이 섞인 백업 | `test_defect_c7_4_…`, `test_defect_c7_6_…` | **유실하고 PASS** — 결함 고정 |
| 7 복구 뒤 중복 수집 | `test_c7_10_…` | 중복 없음 — VERIFIED |
| 8 복구 뒤 누락 | `test_defect_c7_11_…` | **유실하고 PASS** — 결함 고정 |
| 9 readiness 거짓 PASS | 위 결함 셋 + `test_defect_c7_8_…` | 결함 고정 |
| 10 롤백 뒤 재해석 | 없음 | NOT_VERIFIED. 옛 이미지가 새 원장 줄을 읽는 경우는 테스트로 고정되지 않았다 |

계획 §5의 불변식 여덟 개는 C7 설계 §2–§7에 이미 있다. 원장 사슬 검증, 상태보다 원장 우선, 불일치 시 BLOCKED 알림,
멱등, 채택 기록, 복구만으로는 PASS가 아님, 운영자 채택이 그것이다. 대안과 PR별 영향, 줄이는 방안은 설계 §10에 있다.

**D1–D4는 설계 §12의 권장안 그대로 권한다(Q3).** 계획이 요구한 "의사결정 패킷"은 2026-10-11에 그 문서 §12.1로 정리했다.

---

## C. H6 회계 적격성 (계획 Part 3, §6–8)

외부용 렌더(2026-10-11T06:33Z)의 readiness는 다음과 같다. VERIFIED지만 렌더 시점 기준이다.

| 검사 | 값 |
|---|---|
| binance_access, binance_real_schema, cross_source_dedupe, current_health, cutover_boundary, internal_transfer_guard, ledger_chain, ledger_state_consistency, post_cutover_exceptions | PASS |
| shadow_observation | RUNNING — 2.1일 / 3일자 (필요 7일 / 7일자) |
| toss_cash_semantics | WAITING |
| H6b_shadow_ready | false |

- **`binance_real_schema` PASS는 9개 출처 전부의 검증이 아니다.** 행이 있는 법정화폐·Pay·이체 응답의 규칙은 실제 행이 오기 전까지
  NOT_VERIFIED다(#1210). 빈 응답의 PASS는 그 규칙에 대해 아무것도 증명하지 않는다.
- **토스에는 현금 거래 API가 없다.** 공식 스펙 v1.2.24의 엔드포인트 목록에도 없다(§D). 그래서 체결 대비 잔차로만 본다(H6 §2). `cashBuyingPower`의 결제 전후 동작은 관측 전이다. 이것은 H6c의
  하드 게이트(D-H6-7)다.
- **readiness가 true가 되어도 여는 것은 H6b-shadow다. H6c가 아니다**(#1196). H6c의 선행 조건은 다음과 같다.
  - H6b-shadow 운영
  - Toss 의미 확인
  - 미해결 예외 0
  - C7 결정과 그 구현(원장이 회계 권위가 되기 전에 복원 안전성이 필요하다)

  C7 구현을 H6c의 선행 조건에 넣는 것은 이 문서의 권고였고, Thomas가 방향에 동의했다(2026-10-11, 아래 결정 절 Q3).
- 계획 §8의 금지 목록(epoch 시작, 예외 해소, cutover 이동, shadow·H6c 활성화, 원장 수정)은 이 감사에서 하나도 하지 않았다.

---

## D. 토스 주문 능력 (계획 Part 6, §12)

출처는 2026-10-11에 받은 공식 문서다. `developers.tossinvest.com/llms.txt`가 가리키는 `openapi.json` v1.2.24, `overview.md`,
`faq.md`를 읽었다. 엔드포인트는 호출하지 않았다. 부록 B(`MULTI_ASSET_EXPANSION_V0.1.md`, 2026-10-07)의 원문 확인과 맞는다.

| 항목 | 값 | 상태 |
|---|---|---|
| 주문 API | `POST /api/v1/orders` 생성, `…/{orderId}/modify` 정정, `…/{orderId}/cancel` 취소, `GET /api/v1/orders[/{orderId}]` 조회 | VERIFIED (스펙) |
| 국내·해외 | KRX 6자리 코드, US 티커. 국내 캔들·체결은 KRX+NXT 통합 시세다 | VERIFIED (스펙) |
| 주문 유형 | `LIMIT`/`MARKET`. TIF는 `DAY`(기본), `CLS`(US LIMIT만), `OPG`(국내만). US는 금액 주문과 소수점 시장가 매도가 된다(정규장 종료 1시간 전까지) | VERIFIED (스펙) |
| 멱등성 | `clientOrderId`(36자), 10분 유효. 서버는 자동 생성하지 않는다 | VERIFIED (스펙) |
| 조건주문 | `/api/v1/conditional-orders`. 단일·OCO·OTO, 거래소 측 감시 | VERIFIED (스펙) |
| 주문 보조 조회 | 매수 가능 금액, 매도 가능 수량, 수수료, 상·하한가, 매수 유의사항 | VERIFIED (스펙) |
| 실시간 | WebSocket 본인 주문 이벤트 구독 | VERIFIED (스펙) |
| 호출 한도 | `ORDER` 초당 10회, `ORDER_INFO` 초당 6회(09:00–09:10 KST 3회), 공지 없이 바뀔 수 있음 | VERIFIED (스펙) |
| 권한 모델 | OAuth2 Client Credentials, `scopes: {}`. **조회 키가 곧 주문 키다** | VERIFIED (스펙) |
| 토큰 | **클라이언트당 유효 토큰 1개.** 새로 발급하면 직전 토큰이 즉시 `401 token-revoked`가 된다 | VERIFIED (FAQ) |
| 허용 IP | 필수, 미등록 IP는 403 | VERIFIED (FAQ) |
| 모의투자·샌드박스 | 서버는 `openapi.tossinvest.com` 하나다. 세 문서에 모의·sandbox 언급 0건 | VERIFIED (없음) |
| 데이터 이용 | "투자자 본인의 매매 목적에 한하여", 제3자 배포 금지 | VERIFIED (FAQ) |
| 자동매매 자체의 약관상 허용·제한 | 공개 문서에서 명시 문구를 찾지 못했다. 이용약관 원문은 읽지 않았다 | NOT_VERIFIED |
| 본인 계좌에서 실제 주문 가능 여부 | 계좌로 확인하지 않았다. 레버리지·인버스 ETF는 앱에서 사전 등록이 필요하다(FAQ) | NOT_VERIFIED |

설계에 바로 걸리는 사실은 세 가지다.
1. **토큰 하나 규칙.** 주문 프로세스가 자기 토큰을 발급하면 scheduler-maint의 보드 토큰이 죽는다. 반대 방향도 같다. 주문을 연다면
   발급 주체 하나를 공유해야 한다. 그러면 주문 키가 지금 읽기만 하는 maint 평면에 들어온다. 이것은 H1-a(2026-10-07)가 바이낸스에서
   제거한 바로 그 모양이다. 피하는 방법은 토스 Open API 클라이언트를 둘로 나누는 것이다. 클라이언트를 여러 개 발급할 수 있는지는
   NOT_VERIFIED다.
2. **샌드박스가 없다.** 계획의 Stage 4(Broker Sandbox)는 토스에서는 존재하지 않는다. 모의 실행은 자체 시뮬레이터뿐이다.
   첫 실주문이 곧 주문 경로의 첫 검증이다.
3. **규제 기록의 범위.** 부록 B는 조회만 덮는다. 주문을 여는 것은 "강도를 올리는 것이 아니라 새 판단"이다(부록 A·B). 예산 스키마의
   `venue` enum(`binance_futures`만)을 넓히는 거버넌스 변경도 필요하다.

따라서 계획의 기본값 `BROKER_ORDER_CAPABILITY=UNKNOWN`은 **"API는 있음(공식), 계정 검증 없음, 규제 판단 없음"**으로 바꿔 읽는 것이
맞다. 대체 브로커(KIS 모의투자 있음, 키움 REST)는 `MULTI_ASSET_EXPANSION_V0.1.md` §3.1에 웹 조사 수준으로 있다. Thomas가
"토스만 쓰려고 해"(2026-10-07)를 바꾸지 않는 한 다시 열지 않는다.

---

## E. 주식 자동매매 (계획 Part 4–5, 7–11) — 기존 결정과의 대조

| 계획의 요구 | 저장소의 결정 | 상태 |
|---|---|---|
| Phase D–I: 주식 전략·Paper·실거래 | `MULTI_ASSET_EXPANSION_V0.1.md` §5 **P3**: 관문은 연구 일시 중지 종료(2027-03-22)와 크립토 FORWARD_CONFIRMED 1건 이상. D1(2026-10-02): 자산관리(A) 먼저, 알파 확장(B)은 뒤 | BLOCKED |
| 독립 `Stock_AI_System`(자체 Risk Gate·Paper·Reconciliation) | `EQUITY_PERP_LANE_V0.1.md` §1(2026-08-03): `stocks/` 형제 패키지는 **기각**. 이유는 포지션·R 회계·라이프사이클에 권위가 둘이 되기 때문이다. 새 베뉴는 세 이음매로 들어온다: `select_market_data_collector`, `select_account_feed`, `select_order_adapter`. `holdings/`가 따로 선 것(10-02 정정)은 현금 보유가 아무것도 중복하지 않기 때문이었다. 매매 레인은 중복한다. CLAUDE.md: 도메인 패키지는 코어 관문의 적용이지 병렬 런타임이 아니다 | 결정됨 — Q2 아니오 (2026-10-11) |
| Stage 5 Limited Live Canary | 카나리 rung 없음: 2026-09-15 결정, 09-29 재확인(`EXECUTION_STAGE_V0.1.md` 머리말), 10-05 재확인(`PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md` Q1). 이번이 네 번째 요청이다 | 결정됨 — Q4 예, 비승인 기록 (2026-10-11) |
| `stock_*_enabled=false` 기본 상수 | 10-05 기각: 코드 상수는 같은 개념의 두 번째 권위다. 08-10 결정은 "환경이 게이트"다. 계획도 "저장소 설정에 존재한다고 가정하지 않는다"고 적었다 | 기존 결정으로 답이 있음 |
| Strategy E 포트폴리오 리밸런싱 | `TOTAL_ASSET_ALLOCATION_V0.1.md` Q9: 목표 대비 이탈은 **표시만**. 밴드와 BTC 상한은 한도 후보에서 뺐다. 시스템은 비중 한도를 짓지 않는다(Thomas 2026-10-07) | 기존 결정으로 답이 있음 |
| -20% 알림을 거래 중단 조건으로 쓰지 않음 | 계획과 저장소가 같다. P2는 알림만이다 | 일치 |
| Paper가 실주문을 부르지 않음, 비밀은 메타데이터만 | 이미 저장소 규칙이다. paper 격리는 `PHASE_7_14_ALIGNMENT_AUDIT_V0.1.md`의 고정 테스트, 비밀은 CLAUDE.md 가드레일 "Secrets are metadata-only" | 일치 |
| AI가 위험 게이트를 우회하지 못함 | 계획의 문장을 저장소의 특정 테스트나 계약에 대응시키지 않았다 | NOT_VERIFIED (P3 때 대응) |

계획의 재사용 매트릭스(§10)와 전략 후보(§15–16)는 P3 때의 입력으로 남긴다. 지금 쓰면 두 가지 문제가 있다.
- 2027-03-22 이전에 쓰는 설계는 그때의 cohort 판정과 크립토 쪽 증거를 반영하지 못한다.
- 전략 비교(백테스트·walk-forward)는 그 자체가 연구 기계다. P3 관문 전에 하는 것은 D1의 순서를 바꾸는 일이다.

계획의 용어(PreOrderRiskGate, Approval Registry, Kill Switch 등)를 저장소 소유자에 하나씩 대응시키는 일은 이 문서에서 하지
않았다. P3를 열 때 할 일이다.

---

## F. Thomas 결정 항목

각 항목에 권장 기본값을 적었다. "기본값대로"로 답할 수 있다.

- **Q1. 이 계획은 P3의 관문(2027-03-22 + FORWARD_CONFIRMED 1건)을 앞당기는가?**
  - 기본값: **아니오.** 주식 자동매매의 설계·개발은 P3에서 시작한다.
  - 지금 할 수 있는 일은 §D의 능력 기록뿐이다. 이 문서에 이미 있다.
- **Q2. 주식이 P3에서 열릴 때, 2026-08-03의 `stocks/` 기각을 다시 여는가?**
  - 기본값: **아니오.** 새 베뉴는 세 이음매와 `venue` enum으로 들어온다.
  - 현금 주식의 라이프사이클이 R 회계로 표현되지 않는다는 증거가 나오면, 그 증거를 근거로 그때 다시 묻는다.
- **Q3. C7 D1–D4를 설계 §12 권장안대로 결정하는가? C7 구현을 H6c의 선행 조건에 넣는가?**
  - 기본값: **둘 다 예.** 구현 순서는 PR-D → A → B → C다.
- **Q4. 카나리 rung(계획 Stage 5)을 네 번째로 기각한 사실을 기록하는가?**
  - 기본값: **예.** 이 문서의 결정 절에 한 줄 남기고, 실행 단계 사다리는 그대로 둔다.
- **Q5. 계획 §28의 문서 A–F를 새 파일로 만드는가?**
  - 기본값: **아니오.** 새 파일 대신 다음처럼 처리한다.
    - A(§A.2 표)는 H6b-shadow의 테스트로 옮긴다.
    - B는 C7 설계 §12로 갈음한다.
    - C는 H6 문서 머리말로 갈음한다.
    - E는 §D를 부록 B에 덧붙인다.
    - D와 F는 P3 때 쓴다.

---

## 결정 (Thomas 2026-10-11)

- **Q1 — 아니오.** P3 관문(2027-03-22, 크립토 FORWARD_CONFIRMED 1건 이상)을 앞당기지 않는다. 조건을 채우기 전에는 주식 주문 어댑터를
  구현하지 않고, 주문 권한도 넓히지 않는다.
- **Q2 — 아니오.** 기각된 `stocks/` 독립 패키지를 다시 제안하지 않는다. 새 베뉴는 세 이음매(시세·계좌·주문)로 들어온다. 덧붙인 조건은 셋이다.
  - 주식 특화 전략, 거래시간, 결제, 주문 정책, 위험 검사는 **따로 검증한다.** 같은 이음매를 쓴다는 것이 크립토에서의 검증을 물려받는다는
    뜻은 아니다.
  - 크립토 전략과 주문 코드를 주식에 그대로 적용하지 않는다.
  - 크립토의 승인이나 실행 권한(실행 단계, 무장, 예산)을 주식 주문 권한에 상속하지 않는다.
- **Q3 — 조건부.** C7을 H6c의 선행 조건으로 둔다(`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §7의 4단계). 같은 날 D1–D4를 확인했다
  (`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.1).
  - D1: 방향 동의, 조건 다섯.
  - D2: 부분 동의. 별도 키와 UNCHECKED는 정했고, Toss 방식과 cutover는 **미결정**이다(§12.2).
  - D3: 방향 동의. Claude는 채택 명령을 실행하지 않는다.
  - D4: 조건부. 다섯 검증 전에는 백업을 바꾸지 않고, 백업 세션에 운영 변경 권한을 주지 않는다.

  어느 것도 구현 승인이 아니다. C7 구현은 시작하지 않는다.
- **Q4 — 예.** 이 계획의 Stage 5 Limited Live Canary는 기각된 카나리 rung을 다시 요청한 **네 번째** 사례다(09-15 결정, 09-29·10-05 재확인).
  지금은 승인하지 않는다. 앞으로 별도의 명시적 승인이 가능한지는 기존 거버넌스를 따른다. `EXECUTION_STAGE_V0.1.md` 머리말에 한 줄 남겼다.
- **Q5 — 아니오.** 문서 6개를 새로 만들지 않는다. 필요한 내용만 기존 위치에 반영했다.
  - C7 패킷은 `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.1에 넣었다.
  - H6c 선행 조건은 `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §7에 넣었다.
  - 주문 범위 확인 사실은 부록 B(`MULTI_ASSET_EXPANSION_V0.1.md`)에서 이 문서 §D를 가리키게 했다.
  - P3 행에는 Q1·Q2를 적었다.
  - §A.2 시나리오는 H6b-shadow를 지을 때 그 PR의 테스트가 된다. 지금 짓지 않는다.

**장기 목표 (Thomas 2026-10-11).** 토스 기반 주식 자동매매는 장기 개발 목표로 남는다. 단계는 아래 여섯이고, 서로 다른 승인이다. 앞 단계를
마쳤다고 다음 단계가 열리지 않는다.
1. 목표와 연구
2. 아키텍처 검토
3. paper trading
4. 브로커 능력 검증
5. 주문 실행 승인
6. 제한적 실거래

이 문서의 해석으로는 1(주식 연구)부터 4까지가 P3의 일이므로 P3 관문 뒤에 시작한다(Q1). 5는 부록 B 범위 밖의 새 규제 판단이고, 예산 스키마 `venue` enum 확장이라는 거버넌스 변경이 함께 필요하다.
6은 기존 실행 단계 사다리를 따른다. 지금은 어떤 주문 권한도 새로 주지 않는다.

**바뀐 것:** 이 문서, 위 네 문서의 해당 줄, `docs/history/2026-10-11-portfolio-stock-plan-audit.md`. **바뀌지 않은 것:** 코드, 스키마, 정책, 상수, 스케줄, 실행 단계, 운영 상태.

## G. 이 감사가 하지 않은 것

- 런타임, 원장, 상태 파일, 정책, 스키마, 상수는 바꾸지 않았다.
- 토스 엔드포인트를 부르지 않았다. 공개 문서만 받았다.
- LOCAL_ONLY 파일은 열지 않았다. `--full`/`--local`/`--unclassified`도 실행하지 않았다.
- §A.2의 수치는 설계 산식을 따로 계산한 것이다. 저장소 코드의 출력이 아니다.
- 규제·약관 판단은 하지 않았다. 판단은 Thomas의 것이다.
