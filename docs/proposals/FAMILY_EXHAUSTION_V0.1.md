# 제안: family 소진(`SEARCH_EXHAUSTED`) — 상태가 아니라 측정으로 (DRAFT v0.1)

**상태:** DECIDED 2026-09-26 — Q1 B·Q2·Q3 결정, §6 확인 완료(1h는 5심볼 모두가 의도). 남은 구현: Q1 B의 퍼널 family
소진 절(30일 CONFIRMED·마지막 CONFIRMED)과 1h 5심볼 민팅(심볼별 스케줄, 풀링 아님).
**참고:** 이 문서의 나머지는 결정 당시의 근거로 남긴다.
**성격:** 외부 "Crypto Live Trading Follow-up Fix Plan" §20~21(Family Exhaustion)에 대한 답이다.
계획서의 규칙을 운영 저장소에 그대로 대 보면 **rotation family 44개 중 42개가 소진으로
찍힌다.** 가리는 신호가 없다는 뜻이라, 소진을 자동 상태로 만드는 대신 이미 있는 두 장치
(`factory.RETIRED_FAMILIES`, 08-06 tier freeze 판단)에 측정을 붙이고, 예산 재배분은 §18
탐색 예산 제안으로 넘기자는 제안이다.
**측정 시각:** 2026-09-24, 운영 `.runtime_governance_state/`를 `:ro`로 붙인 candidate-963
이미지(main `4a739008`). 판정은 `candidate_ranking.candidate_quality`(holdout 재계산),
attempt 수는 `attempts_by_context`, 퍼널은 `strategy_funnel`.
**근거:** `factory.py`(`RETIRED_FAMILIES`, `context_rotation_index`/`_rotation_offset`,
`POOLED_TIMEFRAMES`), `robustness.selection_adjusted_z`·`selection_rank`,
`docs/TRADING_STRATEGY_REVIEW_RECORD.md`(2026-08-06 tier freeze와 "물량은 통계적 레버가
아니다"), `scripts/verify_factory_tier_freeze.py`, `HYPOTHESIS_TRIAL_V0.1.md`(#962).

---

## 0. 한 줄

지금 이 시스템에서 "개선 없는 family"는 예외가 아니라 전부다. 계보 단위 확정률이 거의 0이라
(3,244계보 중 promotable 1) 계획서 규칙은 family를 가르지 못한다. **가르는 신호는 하나 있다:**
최근 30일 민팅 744행 중 holdout CONFIRMED는 4행이고, 넷 다 OI family 두 개(26행)에서 나왔다.
나머지 718행(96.5%)에서는 0이다. 이 신호는 "누구를 쉬게 할까"보다 "어디에 더 쓸까"의 근거라
§18(탐색 예산)의 입력이다. 소진은 새 상태 없이 기존 은퇴 목록과 정기 측정으로 다루자.

---

## 1. 측정 (2026-09-24)

**저장소:** 3,283행, 160 family.
- rotation 44개(`TEMPLATES`에서 `RETIRED_FAMILIES`를 뺀 것)
- 은퇴 8개
- 나머지는 fusion·mined 자식

**rotation은 family별로 예산이 같다.** 컨텍스트마다 발사 한 번에 연속한 family 블록을 민팅하고
커서가 한 칸씩 전진한다(`context_rotation_index`). 같은 family를 "반복 탐색"하는 구조가 아니라,
모든 family가 같은 몫을 받는 라운드로빈이다. 30일 동안 컨텍스트별로 248행씩, family당 12~24행이
나갔다. 예외는 `rel_strength_long/short`다. 활성 factory 스케줄 세 개의 주(첫) 심볼이 모두 기준
심볼 BTCUSDT라 게이트에 걸리고, 08-09 이후로 0행이다(`REFERENCE_FAMILIES`).

**holdout CONFIRMED(재계산 기준)는 저장소 전체에 7행뿐이다.**

| 민팅 | family | tf·범위 | holdout 거래 | holdout 기대값 |
|---|---|---|---|---|
| 08-14 GEN-833 | oi_unwind_short | 4h·5심볼 | 136 | +0.290R |
| 08-14 GEN-833 | oi_unwind_short | 4h·1심볼 | 146 | +0.284R |
| 08-23 GEN-850 | oi_squeeze_long | 4h·5심볼 | 98 | +0.382R |
| 09-13 GEN-911 | oi_unwind_short | 4h·5심볼 | 27 | +0.645R |
| 09-14 GEN-914 | oi_unwind_short (crossover) | 4h·5심볼 | 29 | +0.719R |
| 09-22 GEN-938 | oi_squeeze_long | 4h·5심볼 | 55 | +0.383R |
| 09-22 GEN-938 | oi_squeeze_long | 4h·5심볼 | 52 | +0.411R |

7행 모두 규칙 해시가 서로 다르다(재채점 중복이 아님).

**30일(08-25 이후) 민팅:**

| 묶음 | 행 | CONFIRMED |
|---|---|---|
| oi_unwind_short, oi_squeeze_long | 26 | **4** |
| 나머지 rotation family 42개 + fusion | 718 | **0** |

**타임프레임별 holdout 통과(퍼널, 풀 밖 계보 기준):**

| tf | 통과/계보 |
|---|---|
| 15m | 0/334 |
| 1h | 0/994 |
| 4h | **6/1,120** |
| 1d | 0/580 |

30일 예산은 4h·1d·1h에 248행씩 똑같이 나뉘었다.

**계획서 규칙을 대 보면:** "최근 10세대 동안 새 robust 후보 없음, 기대값 개선 없음, forward
생존자 없음"은 rotation 44개 중 OI 두 family를 뺀 42개에 참이다. 규칙의 각 항은 다음과 같다.
- **기대값 개선:** family마다 한 번쯤은 holdout 기대값이 양수인 행이 있다. 최댓값은 잡음이라
  "개선"은 날짜만 바꿀 뿐이다(측정표의 `best_holdout_exp` 열).
- **forward 생존자:** cohort 115명이 전원 탐색 단계라 지금은 아무것도 가르지 못한다.
- **novelty:** 아직 측정 장치가 없다.

## 2. 소진이 비싼가 — 08-06 기록이 이미 답했다

소진 family를 계속 캐는 비용으로 떠오르는 첫 후보는 다중 검정 문턱이다. attempt는 family와
무관하게 `(심볼 범위, tf)` 컨텍스트 단위로 세므로, 죽은 family의 민팅이 같은 컨텍스트의 OI
family 문턱도 올린다. 실측으로는 확정 7행이 있는 풀드 4h 컨텍스트의 attempt가 30일 동안
1,322에서 1,570이 됐다. `selection_adjusted_z`는 4.12에서 4.16으로 움직였다.

하지만 이것은 **순위 축(`selection_rank`)**이지 문이 아니다. 승격 문은 holdout CONFIRMED를
읽고, holdout 판정은 attempt를 읽지 않는다. 그리고 `TRADING_STRATEGY_REVIEW_RECORD.md`
2026-08-06이 같은 질문을 이미 쟀다. **mint를 완전히 멈춰도 90일 뒤 필요한 edge는 14.8%만
내려가며, 물량은 통계적 레버가 아니다.** Thomas는 그날 물량 유지를 택했다. 이 제안은 그 판단을
뒤집을 근거를 갖고 있지 않다.

그러니 소진의 비용은 문턱이 아니라 **폭(breadth)**이다. 하루 ~25행의 고정 예산 중 96.5%가
확정을 한 번도 낸 적 없는 family에 간다. 그것이 낭비인지 탐색인지는 예산 배분의 판단이며,
§18의 몫이다.

## 3. 선택지

- **A. 계획서대로 자동 `SEARCH_EXHAUSTED` 상태 + 쿨다운:** 오늘 적용하면 42/44가 쿨다운에
  들어가고 rotation이 OI 두 family로 쪼그라든다. 사실상 예산 결정을 상태 기계가 내리게 된다.
  또 "민팅하지 않는 family"의 권위가 코드(`RETIRED_FAMILIES`)와 상태 파일 둘로 갈린다. **기각
  권고.**
- **B. 측정만 정기화:** family별 소진 표(30일 행, 30일 CONFIRMED, 마지막 CONFIRMED,
  holdout 계보 수, forward 멤버·성숙도)를 퍼널 보고서에 절로 추가한다(읽기 전용). 은퇴는 지금처럼
  측정을 근거로 Thomas가 `RETIRED_FAMILIES`를 고치는 코드 변경으로 한다. 새 상태와 새 권위가 없다.
- **C. 증거 가중 rotation:** 30일 CONFIRMED가 있는 family에 추가 슬롯을 주고 나머지는 최소
  몫(탐색 하한 > 0)을 유지한다. 이것이 외부 계획 §18이 말하는 탐색 예산이다. 별도 제안서가 필요하다.
- **D. 보류.**

**권고(결정은 Thomas):** B를 먼저 하고, C는 §18 제안서에서 OI 신호를 주 근거로 따로 묻는다.
A는 기각한다.

## 4. OI 신호를 읽을 때 주의

- **독립 증거는 7이 아니라 3계보다.** 계보 키(family, 심볼 범위, tf)로 묶으면 3개이고, 나머지
  4행은 형제다. 4h holdout은 이동 창의 마지막 30%(심볼당 1,800봉, 약 300일)라, 며칠 간격으로
  민팅된 형제는 같은 holdout 구간을 거의 다 공유한다.
- OI 이력 자체는 짧지 않다. factory는 거래소 이력(`market_data.DERIVATIVE_HISTORY_DAYS = 1020`일)을
  받아 4h 1,000일 창을 덮는다(`_oi_feed_reaches`: 4h·1h 참, 1d 거짓). 수집 저장소 `oi_store`의
  짧은 시드와는 별개다.
- 같은 OI 계열이라도 `oi_unwind_long`, `oi_squeeze_short`는 30일 동안 각 12행에서 0이다.
  신호는 "OI family"가 아니라 두 family, 한 방향 조합, 4h에 있다.
- 3계보는 모두 이미 forward cohort 멤버다. 계보마다 한 명씩이며, 형제 4행은 계보당 한 명
  규칙으로 빠졌다. cohort와 그 null 쌍둥이가 이 신호의 forward 검증을 맡는다.

## 5. 결정 항목 (Thomas)

- **Q1. 경로:** A / B / C / D. 권고는 B + C를 §18로 넘기는 것.
- **Q2. 은퇴 검토 목록:** 30일 동안 holdout 기대값이 양수인 행조차 0인 rotation family는 다음과
  같다. 30일 행 수를 괄호에 적었다.
  - `session_trend_long`(12), `funding_fade_long`(12), `premium_fade_short`(21)
  - `htf_pullback_long`(12), `oi_unwind_long`(12)
  - `htf_trend_strength_long`(12), `htf_trend_strength_short`(12), `htf_reversal_short`(12)

  이것은 **검토 목록이지 은퇴 권고가 아니다.** 기존 은퇴(`RETIRED_FAMILIES` 주석)는 전제를
  측정한 뒤에 했고, "양수 행 0"은 12행 표본에서는 약하다.
- **Q3. 1h 예산:** 1h는 holdout 통과 0/994이고, 08-06에는 "비용 0.1515R을 신호 +0.0035R 위에
  얹는 티어"라는 근거로 동결됐다. 08-24에 Thomas가 "fast-context supply"로 다시 열었다(1h 라우팅
  슬롯 9개의 공급원). 이 판단의 근거는 routing 공급이지 증거가 아니므로 소진 측정과 충돌하지
  않는다. 다만 아래 관찰을 함께 볼 것.

## 6. 부수 관찰 — 1h 풀드 스케줄은 BTCUSDT만 민팅한다

1h 스케줄의 요청은 5심볼(`BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,DOGEUSDT 1h`)이다. 그런데
`POOLED_TIMEFRAMES = {4h, 1d}`라 `run_factory`는 1h cohort를 무시하고 첫 심볼만 채점한다.
- 30일 동안 1h 248행이 전부 `symbol_scope=['BTCUSDT']`, `symbols_replayed=1`이다.
- 발사마다 나머지 4개 다리를 받아 놓고 버린다.

"1h 라우팅 슬롯 9개의 공급"이 의도였다면 ETH·SOL·BNB·DOGE 1h에는 공급이 없는 셈이다. 소진과는
별개의 문제라 여기서는 기록만 한다. 의도가 BTC 단일이었는지 Thomas 확인이 필요하다.

## 7. B의 구현 비용

- `strategy_funnel`에 family 절 추가(읽기 전용). 입력은 퍼널이 이미 읽는 후보 저장소와 cohort.
- 새 상태, 새 스케줄, 새 권위가 없다. 보드에는 올리지 않는다. 일일 보드는 이미 40줄이 넘고, 이 표는
  주 단위 판단용이다.

## 결정 (Thomas 2026-09-24, 권고대로)

- **Q1 경로: B + C를 §18로.** 자동 `SEARCH_EXHAUSTED` 상태(A)는 기각한다. 퍼널에 family 절을 읽기 전용으로 추가하고, 은퇴는 지금처럼 `RETIRED_FAMILIES` 코드 변경으로 한다. 예산 가중(C)은 `EXPLORATION_BUDGET_V0.1.md`에서 결정했다(보류).
- **Q2:** 8개 목록은 **검토 목록으로만** 두고 은퇴하지 않는다.
- **Q3 1h 예산:** 유지한다. **확인 대기:** 1h 5심볼 스케줄이 `POOLED_TIMEFRAMES` 때문에 BTCUSDT만 민팅하는 것(§6)이 08-24 "1h 라우팅 슬롯 9개 공급" 의도와 맞는지 Thomas 확인이 필요하다. 코드 변경은 확인 뒤에 한다.

## 확인 (Thomas 2026-09-26)

- **§6 / Q3의 확인 대기:** 08-24 "1h 라우팅 슬롯 9개 공급"의 의도는 5심볼 전부였다. 1h는 5심볼 모두에서 민팅하되
  풀링하지 않고, 코호트 스케줄 하나를 단일 심볼 스케줄 다섯 개로 나눈다(시스템 점검 D4). 1h를 풀링하지 않는 이유는
  F9 그대로다: 풀링된 행은 5심볼 범위라 기존 전략과 겹쳐 승격이 막히고(`POOL_CONTEXT_CAP_EXCEEDED`), 방향 조절이
  컨텍스트 단위에서 타임프레임 단위로 줄어든다.
