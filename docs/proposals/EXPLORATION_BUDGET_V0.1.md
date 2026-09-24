# 제안: 탐색 예산 — 계획서의 고정 비율 대신 "fusion이 못 쓴 슬롯"의 용도 (DRAFT v0.1)

**상태:** DECIDED 2026-09-24 — Thomas가 권고대로 결정했다(아래 "결정" 절). 이 문서의 나머지는 결정 당시의 근거로 남긴다.
**성격:** 외부 "Crypto Live Trading Follow-up Fix Plan" §18~19(Exploration/Exploitation
Budget: 구조 탐색 40 / 기존 family 최적화 30 / fusion 20 / exit 10, "탐색 예산 > 0")에 대한
답이다. `FAMILY_EXHAUSTION_V0.1.md`(#965) §3-C가 이 제안으로 넘긴 질문, 곧 "OI 신호에 예산을 더
줄 것인가"도 여기서 묻는다.
**측정 시각:** 2026-09-24, 운영 `.runtime_governance_state/`(스케줄러 원장 archive 포함)와
후보 저장소, main `17933039`. 30일 창은 08-25 이후다.
**근거:**
- `factory.py`: `DEFAULT_BATCH_SIZE=4`, `generate_batch`, `_rotation_offset`,
  `context_rotation_phase`, stop probe 슬롯, 그리고 "the half of the budget fusion declined"
  주석과 topup
- `scheduler.FACTORY_FUSION_PAIRS=4`
- `TRADING_STRATEGY_REVIEW_RECORD.md` 2026-08-06
- #962 `HYPOTHESIS_TRIAL_V0.1.md`, #965 `FAMILY_EXHAUSTION_V0.1.md`

---

## 0. 한 줄

계획서의 네 범주를 지금 코드에 대 보면 이렇다.
- **구조 탐색:** 공급원이 없다.
- **exit 탐색:** 이미 고정 슬롯이 있고 제대로 들어간다.
- **fusion:** 예산은 할당돼 있지만 부모가 말라 8.6%만 쓴다.
- **기존 family 최적화:** 나머지 전부다.

그래서 비율을 정하는 것은 레버가 아니다. 실제로 움직일 수 있는 예산은 하나, **fusion이 못 써서
seeded로 넘어가는 topup(30일 340슬롯, 민팅의 46%)**이다. 코드가 이미 "그 용도를 바꾸는 것은
그것을 주장하는 diff에서"라고 미뤄 둔 자리다. 이 제안은 그 용도를 Thomas가 고르도록 한다.

---

## 1. 측정: 발사 한 번의 슬롯과 30일 실측

발사 한 번은 8슬롯이다. 컨텍스트의 rotation slice 4 family를 한 번씩 뽑는 batch 4개와 fusion
쌍 4개다. fusion이 쌍을 못 채우면 그만큼을 **같은 slice를 한 번 더 뽑는 topup**으로 채운다
(`fused + topup == fusion_pairs`). 30일 동안 활성 컨텍스트 세 개(4h 풀드, 1d 풀드, 1h)에서
93회 발사했다.

| 계획 범주 | 계획 | 지금 장치 | 30일 실측 |
|---|---|---|---|
| 구조 탐색(새 가설) | 40% | **없음.** proposer는 채택 39·설치 0, 09-11부터 멈춤(#962·#963) | **0** |
| 기존 family 최적화 | 30% | rotation batch + topup. 각 draw의 절반은 family의 elite 중심, 절반은 base 중심 | seeded 712행(**95.7%**), 그중 topup 340 |
| fusion/crossover | 20% | `FACTORY_FUSION_PAIRS=4`(발사당 슬롯 절반) | 372슬롯 중 **32(8.6%)**, 행 기준 4.3% |
| exit 탐색 | 10% | stop probe 슬롯(08-30): accept index 4개 중 2개가 family의 stop 상한 쪽을 뽑음 | seeded의 **23%**가 stop ≥ 2.37(fade 계열 probe는 상한 2.0이라 여기서 빠짐) |

읽는 법:
- **"base 중심 절반"은 구조 탐색이 아니다.** 같은 family 안에서 파라미터를 넓게 뽑는 것이다.
  새 조건 조합이나 새 전제는 fusion(말랐음)과 proposer(멈춤)에서만 나온다.
- **fusion 20%는 이미 할당돼 있고, 모자란 것은 부모다.** 부모 자격(`holdout_permits_parenting`)이
  좁아서다(08-06 주석 실측: 1,681행 중 218행). 비율을 올려도 늘지 않고, 부모 문턱을 낮추는 것은
  게이트 완화라 이 제안의 범위가 아니다.
- **exit 탐색은 이미 작동한다.** probe 이전에는 stop 2.1 초과가 0이었다(`factory.py` probe 주석).
- **"탐색 예산 > 0" 원칙:** 파라미터 수준에서는 이미 참(base 절반 + probe)이고, **구조 수준에서는
  거짓**이다. 구조 탐색의 공급원은 #962의 결정에 달려 있다.

## 2. 레버가 아닌 것

- **물량:** `TRADING_STRATEGY_REVIEW_RECORD.md` 2026-08-06의 측정이 있다. mint를 완전히 멈춰도
  90일 뒤 필요 edge는 14.8%만 내려가며, "물량은 통계적 레버가 아니다". Thomas는 그날 유지를
  택했다. 이 제안은 발사당 8슬롯을 바꾸지 않는다.
- **계획서 비율 자체:** 40%는 공급원이 없어 채울 수 없고, 20%는 부모 풀이 정한다. 고정 비율을
  코드에 적으면 그 두 줄은 지켜지지 않는 숫자가 된다.

## 3. 움직일 수 있는 것: topup 340슬롯

지금 topup은 **같은 slice를 한 번 더 촘촘히** 뽑는다(깊이). 코드 주석은 대안을 이렇게 적어 뒀다.
"다음 slice에 쓰면 깊이 대신 폭을 사지만, 그것은 factory가 무엇을 탐색할지를 바꾸는 rotation
변경이라 그것을 주장하는 diff에 속한다."

rotation 한 바퀴는 현재 다음과 같다(mintable family / 발사당 4).

| tf | mintable family | 한 바퀴 |
|---|---|---|
| 4h | 40 | 10발사(10일) |
| 1d | 24(OI·funding 게이트) | 6발사 |
| 1h | 40 | 10발사 |

## 4. 선택지 (topup의 용도)

- **a. 지금대로(깊이):** 같은 4 family를 발사당 두 번씩 뽑는다. family 안에서 elite 중심 근처를
  더 촘촘히 본다.
- **b. 다음 slice(폭):** topup이 다음 4 family를 뽑고 커서가 두 칸 전진한다. 한 바퀴가 절반으로
  준다(4h 10일에서 5일). #965의 family 표가 두 배 빨리 채워지고 통계 비용은 없다(물량 동일).
  - 구현 비용: `_rotation_offset`·`context_rotation_phase`의 산술을 바꿔야 한다. 커서는 지금
    "컨텍스트의 서로 다른 generation 수"라서, 한 발사에 두 slice를 쓰려면 커서 정의가 바뀐다.
  - 커버리지 불변식(`ceil(total / count)` 연속 발사가 라이브러리 전체를 덮음)은 다시 증명해야 한다.
- **c. 증거 가중(착취):** topup을 "최근 K일 안에 holdout CONFIRMED 계보가 있는 family"의 elite
  중심에 쓴다. 장치는 이미 있다(`elite_base_params`). 바뀌는 것은 family 선택뿐이다. 오늘 기준으로
  대상은 `oi_unwind_short`, `oi_squeeze_long`(4h) 두 개다. 비용은 아래 §5.
- **d. 구조 탐색 몫 예약:** #962에서 A(트라이얼 rotation 슬롯)를 고를 때만 의미가 있다. C(트라이얼
  행 + 별도 forward)는 rotation 밖에서 채점하므로 슬롯이 필요 없다.

## 5. c의 비용 — OI 신호가 실제로 얼마인가

#965의 표는 "CONFIRMED 7행, 전부 OI 두 family"다. 예산을 옮길 근거로 읽기 전에 따져 볼 것이
세 가지 있다.
- **독립 증거는 7이 아니라 3이다.** 계보 키(family, 심볼 범위, tf)로 묶으면 3계보이고, 나머지
  4행은 형제다. 형제는 같은 컨텍스트의 **거의 같은 holdout 구간**을 본다. holdout은 이동 창의
  마지막 30%라 4h 풀드에서 심볼당 1,800봉(약 300일)이고, 며칠 간격으로 민팅된 형제는 그 구간을 거의 다
  공유한다. c로 형제를 더 뽑으면 CONFIRMED 행 수는 늘겠지만 같은 구간의 재사용이지 새 증거가
  아니다.
- **3계보 모두 이미 forward에 있다.** 동결된 cohort(09-23, 115명)가 각 계보의 대표를 한 명씩
  담았다. `cand_3b14f39ffeb1f246ce7b`, `cand_4b1a32f15e739d324abe`, `cand_9ac4de0fd54d1661132b`이고,
  null 쌍둥이도 함께 걷는다. OI 신호의 다음 증거는 민팅이 아니라 이 forward 결과다.
- **늘어난 형제는 문에서 쓸 데가 적다.** 풀 문의 family 상한이 점유 2칸이다
  (`OBSERVATION_FAMILY_CAP = 2`). 또 OI family는 1d에서 민팅되지 않는다(`_oi_feed_reaches`
  1d 거짓). 그래서 c는 4h 한 타임프레임의 두 family에 topup을 모은다. OI 이력 자체는 1,020일로
  4h 창을 덮으니, 이력이 짧아서 생긴 신호는 아니다.

## 6. 권고와 결정 항목 (결정은 Thomas)

**권고:**
- 계획서의 고정 비율은 채택하지 않는다.
- topup은 **b(폭)**를 권한다. 물량 불변, 통계 비용 없음이고, family 수준 증거(#965)가 빨리 쌓인다.
- c는 OI 3계보의 forward 판정(cohort·null 대조)이 나올 때까지 보류한다.
- 구조 탐색 몫은 #962의 결정을 따른다.

**결정 항목:**
- **Q1.** 계획서 고정 비율(40/30/20/10)을 기각하는가.
- **Q2.** topup의 용도: a / b / c.
- **Q3.** (c일 때) K(최근 며칠의 CONFIRMED를 볼지)와 topup 중 몇 슬롯을 쓸지. 나머지는 폭이나
  깊이로 둔다.
- **Q4.** 구조 탐색 몫: #962 Q1이 A면 슬롯 상한(권고 4)을 이 예산에서 뺄지, 발사당 8에 더할지.
  후자는 물량 증가라 08-06 판단과 함께 봐야 한다.

## 7. 범위 밖

novelty(§16~17)와 연구 에포크(§30~31)는 다루지 않는다. 오늘 있는 "새로움" 장치는 stop probe
하나이고 파라미터 수준이다. 구조 수준 novelty는 구조 탐색 공급원이 생긴 뒤에야 잴 대상이 생긴다.

## 결정 (Thomas 2026-09-24, 권고대로)

- **Q1:** 계획서의 고정 비율(40/30/20/10)은 **기각**한다.
- **Q2 topup 용도: b(폭).** topup은 다음 slice를 뽑고 커서는 두 칸 전진한다. 구현 PR은 커버리지 불변식 테스트를 먼저 쓰고, 첫 발사의 민팅 구성을 확인한다.
- **Q3:** c(OI 가중)는 **보류**한다. OI 3계보의 forward 판정(cohort·null 대조)이 나온 뒤 다시 묻는다.
- **Q4:** #962가 C로 결정됐으므로 해당 없음. 트라이얼은 rotation 밖에서 채점하고 슬롯을 쓰지 않는다.
