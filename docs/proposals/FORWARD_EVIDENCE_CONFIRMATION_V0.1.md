# 제안: Forward Paper 증거가 무장 문에 닿으려면 인정 규칙이 필요하다 (DRAFT v0.1)

**상태:** DRAFT — 설계 선행(구현 아님). 코드 변경 없음. 정책 활성화 없음. Thomas 결정 대기.
**성격:** 게이트 완화가 아니다. **같은 문턱을 다른(더 오염되지 않은) 데이터에 적용하는 규칙**을
새로 정의하는 제안이며, 무장은 지금처럼 운영자 승인 문(R9 ask → `/approve`)에 남는다.
**측정 시각:** 2026-08-11, `thomas-scheduler` 컨테이너 및 `.runtime_governance_state/`.
**근거:** `crypto/pool.py`(promotable_backlog, live_tier), `crypto/robustness.py`
(MIN_HOLDOUT_TRADES=25, CONFIDENCE_Z=1.96, MIN_HOLDOUT_PERIODS=8), #648(OBSERVATION/LIVE
티어 분리), #681(`live_armed_strategies` 행), `scripts/arm_live_tier.py`(홀드아웃 게이트),
Thomas 2026-08-11 지시("게이트는 낮추지 않는다 / OBSERVATION 계층을 적극 활용한다 /
Final Test는 개발 중 절대 보지 않는 구간으로").

---

## 0. 한 줄

지금 무장 문은 **백테스트 홀드아웃의 CONFIRMED만** 읽는다. OBSERVATION 티어가 쌓는 forward
paper 증거는 어떤 규칙으로도 그 문에 닿지 않으므로, **쌓이기만 하고 문을 열 수 없다** —
인정 규칙이 정의되기 전에는 "OBSERVATION 적극 활용"이 무장으로 이어지는 경로가 존재하지
않는다.

---

## 1. 측정된 현재 상태 (2026-08-11)

- 후보 1,993건, holdout **CONFIRMED 0건** (INSUFFICIENT 1,228 / CONTRADICTED 592 /
  블록 없음 173). 판정 가능했던 592건은 전원 CONTRADICTED — 게이트가 정상 작동한 결과이며,
  게이트 유지 지시와 일치한다.
- INSUFFICIENT 분해: 홀드아웃 표본 <25거래 692건(1d 티어는 324건 중 202건, 62%가 여기),
  stdev 미기록 스키마 빈티지 536건(재민팅 없이는 영구 판정 불능).
- 점유 5개 전략 전원 `OBSERVATION / holdout=INSUFFICIENT` — forward 증거가 자라는 중이지만
  **그 증거의 소비자가 없다**: 승격/무장 경로의 홀드아웃 게이트는
  `candidate_quality`(백테스트 증거)만 읽고, forward paper 결과는 lifecycle 강등에만 쓰인다.
- forward 속도 한계: 4h 라인이 실시간 25거래에 도달하는 데 수개월(H3). 이 제안은 그 속도를
  바꾸지 못하며, 바꾸려는 제안이 아니다.

## 2. 왜 forward가 Locked Final Test인가

현 표본 기하에서 4번째 백테스트 파티션(Train → Validation → Locked Final)은 역효과다:
30% 홀드아웃도 692건이 25거래 미달인데 또 쪼개면 INSUFFICIENT가 악화되고, 최종 구간이
1–2 시장기간이면 유효표본 ~10기간 체제에서 0.15R 미만은 원리적으로 판정 불능이다.
반면 **민트 시점 이후의 forward 데이터는 정의상 개발 중 볼 수 없었던 구간**이다 — 재사용
오염(A2)이 원천적으로 없다. "절대 보지 않는 최종 시험"의 정직한 구현이 이미 OBSERVATION
티어에서 돌아가고 있고, 없는 것은 그 결과를 읽는 규칙뿐이다.

## 3. 제안하는 규칙 (초안 — 문턱은 홀드아웃과 동일)

Forward 확증(FORWARD_CONFIRMED)은 다음을 **전부** 충족할 때만 성립한다:

1. **표본:** 민트 이후 forward paper 청산 ≥ `MIN_HOLDOUT_TRADES`(25). 현행 상수를 그대로
   가져온다 — 새 문턱을 만들지 않는다.
2. **노이즈 클리어:** 순 R(현행 요율, 수수료+슬리피지+캐리)이 자기 분산의 95% 구간
   (`CONFIDENCE_Z`=1.96) 밖에서 0을 배제.
3. **기간 분포:** 홀드아웃의 `MIN_HOLDOUT_PERIODS`(10중 8)에 상당하는 조건이 필요하나,
   forward는 창이 짧아 **기간 축의 재정의가 본질적 설계 결정**이다(§5-1). 초안:
   달력 주 단위로 나눠 활동 주의 80% 이상에서 방향 일치.
4. **반증 대칭:** 충분 표본에서 음수면 FORWARD_CONTRADICTED — 해당 라인은 인정 경로에서
   제외되고 재민팅 대상이 된다. 확증만 읽고 반증을 안 읽는 규칙은 게이트 완화다.

**소비 지점:** `arm_live_tier`/승격 문의 홀드아웃 게이트가 `holdout=CONFIRMED **or**
forward=CONFIRMED`를 읽는다. 그 외에는 아무것도 바뀌지 않는다 — 자동 무장 없음,
`live_tier`의 자동 기록자는 지금처럼 `pool.disarm_live_tier`(OBSERVATION 방향)뿐.

**인간 선택 편향의 청구:** forward 성적을 보며 라인을 고르는 행위 자체가 다중 시도이므로,
FORWARD_CONFIRMED 판정에는 관측 대상이었던 라인 수(attempt count)를 기록에 남긴다 —
A1(다중검정 부채)과 같은 원리, 같은 장부.

## 4. 이 제안이 바꾸지 않는 것

- 홀드아웃/ROBUST/비용 게이트의 문턱 — 숫자 하나 움직이지 않는다.
- CONTRADICTED(백테스트든 forward든)의 제외.
- 무장의 운영자 문(R9 ask → Thomas `/approve`)과 승인 해시.
- Research 피드백과 Live 권한의 분리 — 자동으로 열리는 것은 계속 없다.

## 5. 열린 결정 (Thomas)

1. **기간 축 재정의**(§3-3): 달력 주? 시장 레짐 슬라이스? 최소 forward 일수는?
2. **OBSERVATION 슬롯 확대 여부:** 현행 점유 5. 확대 시 유사중복(거의 동일 거래 62그룹)
   제외 규칙이 선행되어야 슬롯이 사본으로 차지 않는다.
3. **INSUFFICIENT 후보의 OBSERVATION 입장 기준:** 비용 기준 CURRENT + 깊이 FULL +
   양의 기대값(현행 요율)을 최소선으로 제안 — CONTRADICTED는 입장 불가.
