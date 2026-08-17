# 근거 정리: funding_zscore의 시간 기저 재결정 (DRAFT v0.1)

**상태:** 결정 대기 (Thomas) — 이 문서는 결정하지 않는다. 2026-08-17 크립토 레인 감사가
확정(적대 검증 CONFIRMED)한 결함 하나 — *`funding_zscore`만 홀로 bar space에 남아, 같은
임계값이 타임프레임마다 다른 통계를 의미한다* — 의 재결정 근거를 한곳에 모은 것이다.
수치의 재현 방법은 §5에 전부 있다.

## 0. 한 줄

`funding_zscore`는 8시간마다 한 번 정산되는 시리즈를 바(bar) 단위 rolling z로 읽어, 같은
`|z| ≥ 1.0` 게이트가 4h에서는 **16.7일 norm(정산 50개)**, 1d에서는 **100일 norm(정산
300개)** 을 의미하고 두 기저의 게이트 판정은 바의 15~20%에서 갈린다 — 그러나 이 칼럼을
읽는 저장 스펙 62개 중 **활성 풀 0, 무장 0, ROBUST 0** 이라, 지금이 이 수정이 가장 쌀
시점이다.

## 1. 결함의 실체 — 모듈 자신의 규칙을 자신만 안 지킨다

- [`features.py:933`](../../runtime/mvp_runtime/crypto/features.py): `funding_zscore =
  indicators.zscore(funding_rate, 100, 10)` — `funding_rate`는 as-of 정렬된 **per-bar**
  칼럼이다. 정산은 8시간마다이므로 100-bar 창이 담는 실제 정산 수는 15m ~4개 / 1h ~13개 /
  4h 50개 / 1d 300개.
- 같은 파일이 이 결함을 **두 번 이름 붙여 고쳤다**: open interest(§963-976, "the z-score's
  window would mean 30 daily observations rather than 30 repeats of one")와 positioning
  (§553-556, "derive in the series' own time base, then align"). funding만 남았다.
- [`features.py:163-165`](../../runtime/mvp_runtime/crypto/features.py)의 주석은 반대를
  주장한다("Windows match the funding z-score deliberately: both describe a crowding
  pressure on its own cadence") — positioning엔 참, funding엔 거짓. 어느 안이 채택되든 이
  주석은 정정 대상이다.
- **출처**: C3 소스-패리티 포팅(픽스처 `tests/fixtures/crypto_indicator_parity.json`이
  고정)이 event-space 교훈보다 먼저 있었다. 즉 이것은 결정된 예외가 아니라 교훈 이전의
  잔재다. BUILD_HISTORY가 다룬 것은 funding의 **타이밍**(계단함수 → `premium_index`를 bar
  해상도로 신설)이지 창의 **의미**가 아니다.

## 2. 노출도 — 재는 지금이 가장 싸다 (2026-08-17 측정)

`funding_zscore`를 entry 조건으로 읽는 저장 후보(중복 제거 2,089개 중 **62개**):

| family | tf | n | exp>0 | mean exp | 판정 |
|---|---|---|---|---|---|
| funding_fade_long | 15m | 4 | 0 | −0.320 | PROVISIONAL 4 |
| funding_fade_short | 15m | 4 | 0 | −0.316 | PROVISIONAL 4 |
| funding_fade_long | 1h | 8 | 2 | −0.115 | PROVISIONAL 8 |
| funding_fade_short | 1h | 9 | 3 | −0.119 | PROVISIONAL 9 |
| funding_fade_long | 4h | 15 | 6 | **+0.017** | PROV 12 / FRAGILE 3 |
| funding_fade_short | 4h | 15 | 2 | −0.175 | PROV 11 / FRAGILE 4 |
| (fusion 파생 3건 포함) | | | | | |

- **1d 0건, 활성 풀(101) 중 0건, 무장 0건, ROBUST 0건.** 라이브·풀 노출이 전혀 없다.
- `funding_momentum_long/short`(#604, 2026-08-08 신설 — "이 어휘에서 가장 싼 진짜 새
  가설")는 아직 **mint 0건**. 템플릿 리스트 순서상 비-가격 신생 계열이 늘 마지막에
  쌓이는 그 자리다. 즉 이 칼럼의 미래 소비자는 이미 예약돼 있고, 지금 고치지 않으면
  momentum 계열이 잘못된 기저 위에 증거를 쌓기 시작한다.
- 교차 참조: funding_fade는 RSI confirm 슬롯을 쓰는 faded 계열이라, RSI 추첨 질량
  재배분(TEMPLATE_RSI_DRAW_MASS_V0.1)의 결정과 mint 질량이 겹친다.

## 3. 측정 — 두 기저는 실제로 얼마나 다른가 (실피드, 2026-08-17)

프로덕션과 동일한 함수(`_asof_align` + `indicators.zscore(100, 10)`)로 bar-space와
event-space(정산 시리즈에서 z 계산 후 정렬)를 나란히 계산:

| 셀 | 상관 | mean\|Δz\| | max\|Δz\| | \|z\|≥1.0 게이트 불일치 | bar-fire 중 event 확인 | 창이 담는 정산 |
|---|---|---|---|---|---|---|
| BTCUSDT 4h | 0.885 | 0.36 | 9.4 | **15.3%** | 77.2% | 50 |
| ETHUSDT 4h | 0.893 | 0.34 | 10.1 | **14.3%** | 76.6% | 50 |
| BTCUSDT 1d | 0.861 | 0.49 | 4.3 | **19.8%** | 74.0% | 300 |

- 감사 검증자의 재현(동일 정산 시리즈 하나): 같은 현재 정산이 15m/1h/4h/1d에서 z =
  1.60 / −0.70 / −1.97 / 0.09로 읽혔다(event-space: −0.97). 하나의 상태, 네 개의 답.
- 의미: 오늘 mint되는 `funding_z_min ≈ 1.0~2.0` 임계값은 4h와 1d에서 **다른 질문**이고
  (16.7일 vs 100일 crowding norm), 두 기저의 진입 판정은 바 6~7개 중 1개에서 갈린다.
  event-space로 통일하면 100정산 = 33.3일 norm이 모든 타임프레임에서 동일해진다.
- backtest와 live는 같은 칼럼을 읽으므로 **backtest/live 괴리는 없다** — 결함은
  타임프레임 간 캘리브레이션과 (로테이션 밖) 15m/1h의 통계적 퇴화다.

## 4. 선택지와 비용

**A안 — 현상 유지 + 주석만 정정.** 비용 ~0. §3의 교차-타임프레임 불일치가 미래 mint
(특히 momentum 계열)에 그대로 상속된다. 62/62가 ROBUST에 못 미친 계열에 더 쓸 게
없다고 판단하면 정합적 — 단 그 판단은 #604가 momentum을 신설한 논거("fade의 실패는
반대 가설의 증거가 아니다")와 충돌한다.

**B안 — 제자리 전환 (bar → event space).** `_positioning_columns`와 같은 6줄 패턴.
무효화되는 것: 저장 62행의 미래 재생(null_control post-mint 시리즈가 불연속) — 풀·라이브
소비자는 0이므로 **운영 영향 없음**. C3 패리티 픽스처 재고정 필요(패리티는 소스의 자기
결함까지 복제했다 — 모듈이 그 뒤 event-space를 자기 표준으로 두 번 채택했으므로,
패리티 항목 하나를 의도된 이탈로 기록하는 것). 신규 mint의 임계값이 전 타임프레임에서
같은 의미가 된다.

**C안 — 병행 칼럼 (`funding_zscore_event`) 신설, 신규 mint만 사용.** premium_index
선례(바꾸지 않고 옆에 신설)의 반복. 저장 62행의 재생 불변이 장점이나, 그 62행을 읽는
운영 소비자가 없으므로 보호 대상이 사실상 없다. 비용: 어휘 +1, 한동안 funding z 두 벌
(G2/G4가 정리한 "읽는 곳 없는 칼럼" 클래스를 하나 새로 만드는 셈).

**D안 — mint 어휘에서 제거.** 62/62 비-ROBUST가 근거. 단 momentum 계열이 mint 0인
상태로 죽는다 — 9일 전 #604가 정확히 반대 결정을 했으므로, D는 이 문서가 아니라 #604의
재결정으로 다뤄야 한다.

**권고 (구속력 없음): B안.** 운영 노출 0인 지금이 이 전환이 가장 싼 순간이고, 기다리는
비용은 momentum 계열이 잘못된 기저 위에 증거를 쌓는 것이다. C안이 사는 보호(62행의 재생
연속성)는 소비자가 없고, A안은 결함을 미래로 상속한다. B안 채택 시 같은 PR에서:
163-165 주석 정정, 패리티 픽스처 재고정, `FUNDING_Z_WINDOW` 주석에 "100 **정산** =
33.3일, 전 타임프레임 동일" 명기.

## 5. 재현

- 노출도: `pool.read_candidates` 위에서 entry_rules conditions에 `funding_zscore`가 있는
  행을 세고 `load_active_pool`과 대조 (§2의 표).
- 괴리: 스케줄러 컨테이너에서 `collect_market_data` + `collector.funding_history(records=
  _FUNDING_RECORDS)` → 프로덕션 경로 그대로 `_asof_align` 후 `zscore(100, 10)`(bar-space)
  vs 정산 시리즈에 `zscore(100, 10)` 후 `_asof_align`(event-space), 게이트 불일치는
  `|z| ≥ 1.0/1.5`에서 비교 (§3의 표).
- 창-정산 수: 각 바의 100-bar 창에 든 정산 타임스탬프 수의 중앙값.
