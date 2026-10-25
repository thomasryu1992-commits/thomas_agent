# 제안: temporal_stability를 측정하고 나서 판정한다 — 워크포워드 period 소계 (DRAFT v0.1)

**상태:** IMPLEMENTED 2026-10-25 — **닫았다: PR-2 없음(W1, Thomas 2026-10-25).** §4 보고서를 처음 돌렸더니(2026-10-25)
유효한 증거로는 판별력이 없었다. 그대로 읽으면 CONFIRMED가 높아 보였지만 CONFIRMED 14개 중 12개가 OI lookahead(INVALID)였고,
그것을 빼면 CONFIRMED는 2개이며 중앙값이 CONTRADICTED보다 낮다(아래 "§4 결과" 절). PR-1(기록 전용)은 구현돼 그대로 둔다.
다시 여는 조건은 유효한 홀드아웃 CONFIRMED가 30개 이상 쌓이는 것이다.
**원래 상태(작성 당시):** DRAFT — PR-1(기록 전용)은 구현됨; **PR-2(판정 활성화)는 아래 §4의 측정 결과를 근거로
Thomas가 결정한다.** 이 문서가 그 결정의 게이트다.

**근거:** `robustness.py` 헤더가 스스로 명시해 온 공백 — `temporal_stability`는 소스의
워크포워드 모듈이 이식되지 않아 항상 None이고, `insufficient_walk_forward_evidence` 경고가
모든 후보에 붙는다(가중치 0.25의 `temporal_consistency` 항이 pass_rate 반쪽으로만 돈다).
외부 참조: freqtrade FreqAI의 `split_timerange`(구간을 연속 세그먼트로 쪼개 세그먼트별 성과의
안정성을 본다). 재학습 루프는 가져오지 않는다 — 우리 스펙은 고정 데이터이고, 민팅 이후 순수
표본은 `forward_confirmation`이, 선택 편향 청구는 A1(attempt count)이 담당한다.

---

## 0. 한 줄

홀드아웃 꼬리는 이미 market period 단위로 소계된다(`HOLDOUT_PERIODS`, 2026-08-04/06 실측이
근거). 같은 소계를 **스코어 구간(train)에도** 기록하고, 그 위의 판정(temporal_stability)은
스토어가 점유율·판별력을 실측한 **다음에만** 켠다 — `HOLDOUT_PERIODS`가 5에서 10으로 움직인
것과 같은 순서(측정 → 이동).

## 1. 설계 (PR-1, 구현됨)

- `factory.WALK_FORWARD_PERIODS = 20` — train 구간을 등봉 20슬라이스로 소계. 폭 정합이 논거다:
  train은 창의 70%(꼬리는 30%)이므로 20슬라이스 ≈ 35일/슬라이스 — `HOLDOUT_PERIODS` 위
  자기상관 표의 '30슬라이스/33일' 행(약한 평균회귀 = 보수적)이고, 표가 경고한 50-67일 밴드를
  피한다.
- `walk_forward` 블록에 `period_r` / `period_trades` / `periods` / `periods_judged` 기록.
  홀드아웃 소계와 같은 필드명·같은 클램프 규칙(짧은 레그가 폭을 정하고, 늦게 닫힌 거래는
  마지막 슬라이스로 클램프)·net R. **`temporal_stability`는 None 유지** — scorer는 부재
  증거로 읽으므로 어떤 점수·판정도 움직이지 않는다.
- 격리는 공짜로 고정된다: 홀드아웃 스위트의 "꼬리만 바꾸면 walk_forward는 동일" 테스트가
  새 필드를 자동으로 덮는다(`test_the_score_never_sees_the_holdout_bars`).

## 2. 판정식 (PR-2가 켜는 것)

```
judged  = period_trades[k] ≥ 1 인 슬라이스          # 빈 슬라이스는 탈락 — 증거의 부재
temporal_stability = (judged 중 period_r > 0 비율)
None    if judged 수 < WALK_FORWARD_MIN_PERIODS(8)   # 0이 아니라 None — 측정 불능
```

- 빈 슬라이스 탈락은 `robustness._periods_confirm`의 명문 선례. 0점 처리는
  `_oi_feed_reaches`가 기록한 사고(피드가 최신 구간만 덮어 멀쩡한 패밀리가 FRAGILE 은퇴)를
  재현한다.
- 부호 비율(크기 아님): 1~2건짜리 슬라이스의 분산 추정은 소음이고, period 단위 크기 검정은
  홀드아웃의 `_periods_confirm`(t-구간)이 이미 담당한다. period 균등 가중 — 독립의 단위는
  거래가 아니라 period(분산 팽창 10-15배 실측).
- 플로어 8은 `MIN_HOLDOUT_PERIODS`의 해상도 논증 재사용이며, 한 달 반짝 거래한 스펙이
  슬라이스 3개로 만점을 사는 것을 막는다.

## 3. 켜졌을 때 움직이는 것 / 안 움직이는 것

- scorer 코드 변경 0줄: `_temporal_consistency`가 present 신호를 평균하는 기존 계약 그대로.
  `insufficient_walk_forward_evidence` 경고는 판정 가능한 후보에서만 사라진다.
- **ROBUST는 어느 쪽이든 홀드아웃 게이트 뒤다.** 움직이는 것은 PROVISIONAL/FRAGILE 내부
  순위뿐. scorer 버전은 v1 유지 — cost_robustness가 살아났을 때처럼 **필드의 존재가 증거의
  세대를 말한다**(`walk_forward.period_r` 유무). 구 레코드 소급 재채점 없음; 수렴은 일일
  재민팅(cost basis 선례).

## 4. 측정 계획 (사전 등록 — `scripts/walk_forward_stability_report.py`, 읽기 전용)

PR-1이 며칠 축적된 스토어에서 세 가지를 읽는다. **이 세 수치가 PR-2 제안의 전부다.**

1. **점유율** — `periods_judged` 분포(전체·타임프레임별). 특정 타임프레임이 플로어 아래에
   구조적으로 몰리면 그것은 상수(20/8)를 움직일 일이지 그 스펙들을 "불안정"으로 읽을 일이
   아니다.
2. **판별력** — would-be temporal_stability를 홀드아웃 판정별(CONFIRMED vs CONTRADICTED)로
   분할. 홀드아웃은 이 지표의 입력이 아니므로 정당한 검증 대상이다. **CONFIRMED가
   CONTRADICTED보다 뚜렷이 높지 않으면 지표는 이 스토어에서 소음이고, PR-2는 없다.**
3. **이동폭** — 오늘 켰다면의 점수 델타 분포와 판정 플립 수(저장된 components/weights로
   temporal_consistency 항만 재유도한 카운터팩추얼).

판별력 분할은 PR-2 **제안**의 근거로만 쓴다 — 판정식 자체를 그 분할로 튜닝하는 순간
FACTORY_ABLATION §1-3이 거절한 A2(홀드아웃 재사용) 증폭이다.

## 5. 비목표

- 재선택 워크포워드(freqtrade FreqAI의 학습창별 재탐색): "탐색 프로세스가 수익적인가"라는
  다른 질문. temporal_stability가 §4를 통과하지 못할 때만 재검토.
- `_prior_window_evidence` 변경: "보고하되 판정하지 않는다"는 그 모듈의 계약 유지.
- `walk_forward_pass_rate`(3윈도우) 변경/제거: 세대 간 비교 가능성 유지.

## 결정 (Thomas 2026-10-06, `SYSTEM_SCORECARD_V0.1.md` Q3 — §3 처리안 권고대로)

- **관문을 바로 적는다.** PR-2를 막는 것은 Thomas의 답이 아니라 §4 보고서가 한 번도 돌지 않았다는 사실이다. 보고서는 읽기
  전용이라 D3의 측정 예외(2026-10-01)에 든다. 돌린 뒤 §4의 2(판별력)가 없으면 PR-2 없이 이 문서를 닫는다.
- **실익이 줄었다.** LIVE 문이 FORWARD_CONFIRMED만 받게 된 뒤로(2026-09-30) PR-2는 홀드아웃 판정 안의 순위만 바꾼다. 판별력이
  있어도 판정을 켜는 일은 완화가 아니라 강화이지만, 켤지는 보고서를 본 뒤 Thomas가 정한다.

## §4 결과 (2026-10-25, 처음 실행) [확인, 레이크 `20261025T071701Z`]

보고서는 샌드박스에서 돌렸다(`sandbox_run.sh -- python -m scripts.walk_forward_stability_report`). 소계가 있는 행은
3,156개다(2026-08-20 ~ 10-24).

1. **점유율:** 플로어(8) 아래는 194/3,156이다. 1h 76/1,851, 4h 27/775, 1d 91/530. 1d만 17%로 조금 몰리지만, 상수를
   옮길 만큼 구조적이지 않다.
2. **판별력: 유효한 증거로는 없다.**

   | 홀드아웃 | 그대로 (n, 중앙값) | INVALID 뺌 (n, 중앙값) |
   |---|---|---|
   | CONFIRMED | 14, 0.700 | 2, 0.400 (둘 다 BIASED) |
   | INSUFFICIENT | 378, 0.500 | 322, 0.474 |
   | CONTRADICTED | 1,874, 0.450 | 1,834, 0.450 |

   - CONFIRMED 14개 중 12개가 `oi_lookahead`(INVALID)다. lookahead는 채점 구간과 홀드아웃에 같은 미래를 흘려, 지표와
     정답지를 함께 올린다. 그대로 읽은 AUC 0.89는 이 12개가 만든 것이다. 남은 둘로는 0.39다.
   - 보고서는 이제 INVALID를 빼고 나눈다(`candidate_ranking.evidence_standing`). CONFIRMED 줄에는 BIASED 수가 붙는다.
   - 유효한 CONFIRMED가 3,156개 중 2개뿐이라, 기다려도 이 질문에 답할 표본이 언제 쌓일지 알 수 없다.
3. **이동폭:** 지금 켜면 점수 차이 중앙값은 +0.0000, 범위는 −0.096 ~ +0.073이다. 판정이 바뀌는 행은
   FRAGILE→PROVISIONAL 4개다.

| 결정 | 질문 | 권고 |
|---|---|---|
| **W1** | 2026-10-06 규칙("판별력이 없으면 PR-2 없이 닫는다")대로 이 문서를 닫는가 | **예.** PR-2 없이 닫는다. PR-1의 소계 기록은 그대로 둔다. 다시 열 조건은 유효한 홀드아웃 CONFIRMED가 30개 이상 쌓이는 것이다 |

## 결정 (Thomas 2026-10-25) — W1 예

"승인: #1302, #1303, #1304 병합, W1 예(PR-2 없이 닫음) (Thomas 2026-10-25)"

- **W1 예:** 2026-10-06 규칙대로 PR-2 없이 이 문서를 닫는다. `temporal_stability`는 계속 None으로 기록되고 판정에 들어가지 않는다.
- PR-1의 소계 기록(`walk_forward.period_r`/`period_trades`)과 보고서(`scripts/walk_forward_stability_report.py`)는 그대로 둔다.
- 다시 여는 조건: 유효한(INVALID가 아닌) 홀드아웃 CONFIRMED가 30개 이상 쌓이면 보고서를 다시 돌려 §4의 2를 읽는다.
