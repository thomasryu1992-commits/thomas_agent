# 오토 트레이딩 전략 검토 기록 — 개선 필요 지점

**Status:** Record / notes only · **Normative authority:** None · **Owner:** Thomas
**검토일:** 2026-07-30 · **검토 범위:** `runtime/mvp_runtime/crypto/` (38 modules, ~17.7k lines)

> **구현 상태 (2026-07-30):** **B2가 반영됐고, 그 과정에서 이 문서에 없던 문제 하나가 더 나왔다.**
>
> B2는 "paper R은 비용 0인데 강등 판정이 그 위에서 이뤄진다"였고, 이 문서는 그것을 *정합성*
> 문제로 적었다. 측정해 보니 **규모의 문제**였다 — 이 런타임의 자체 페이퍼 86건은 gross
> +0.041R, 저장소의 비용 모델을 적용하면 **−0.506R/건**이다. 임계치(`warn 0.0R`)는 그대로
> 두고 입력만 net으로 바꿨다(`cost.outcome_net_r`, 읽는 시점에 현재 요율로 환산 —
> `pool.expectancy_at`과 같은 방식). 저장되는 `result_R`은 바꾸지 않았다.
>
> 그리고 이 문서가 놓친 것: **비용이 R의 몇 %인가는 전략의 성질이 아니라 스톱 거리의 성질이다.**
> `1R = stop_atr × ATR`이므로 짧은 타임프레임은 같은 bps를 더 작은 수로 나눈다. 현재 비용 기준
> 후보의 거래당 비용은 15m 0.341R / 1h 0.168R / 4h 0.077R / 1d 0.029R — **12배 차이**인데
> gross 기대값은 +0.01~+0.23R 범위에 몰려 있다. 즉 순 기대값이 사실상 비용의 함수다. 그래서
> 사후 강등만으로는 늦고, 진입 단계에 산수 문턱을 하나 뒀다(`cost.MAX_ENTRY_COST_R = 0.25`,
> paper와 `live_entry` 양쪽 문에서 동일 상수). 거부는 counterfactual 레지스트리에 shadow로
> 남으므로 문턱이 너무 조이면 그것도 측정된다.
>
> **갱신 (2026-08-03):** 그 사이 B1(펀딩, `cost.funding_cost_r`)과 F1 일부
> (`paper.MAX_DIRECTIONAL_SKEW`)가 들어갔고, `stop_atr` 하한이 1.2로 올라갔다. 그리고 이 날
> 세 건이 더 들어갔다 — **A3**(#438: 홀드아웃 3거래·`total_R > 0` → 25거래 + 자기 분산의 95%
> 구간이 0을 배제. 저장된 `holdout_status` 라벨을 읽던 것도 재계산으로 바꿨다. 라이브 승격
> 대기열 5 → 0), **A1**(#440: `(symbol_scope, timeframe)`별 시도 수를 읽는 시점에 세어
> Bonferroni 문턱을 랭킹 tier로. 차단은 아직 안 한다), **D1 일부**(`ExitRules`에
> `breakeven_at_r` / `trail_atr`. 팩토리는 아직 mint하지 않고, 라이브 문은 거부한다).
>
> A1을 열게 한 관측은 이 문서에 없던 것이다: 후보 979개를 백테스트 거래수로 나누면 기대값이
> **단조 감소**한다(<20건 +0.114R / 20-49 +0.093R / 50-199 −0.003R / 200+ −0.212R). 표본이
> 늘수록 진실에 가까워진다는 뜻이고, 그 진실은 대략 거래비용이다. A1은 "보정이 없다"는 지적으로
> 적혀 있었지만 실제로는 **모집단에 엣지가 없다**는 관측이었다.
>
> **아직 안 된 것:** B3(슬리피지 실측), B4(TP 미체결), C1(체결 편차 가드), A2·A4~A6(통계),
> F2·F3(포트폴리오), D2~D4·E·G·H 전부. C2는 **닫히지 않았고 표면화만 했다** —
> `r_basis: filled`(라이브)는 비용 환산에서 제외되고, 가격이 없어 환산 못 한 행 수는
> `r_basis_gross_rows`로 보고된다. 보드도 gross/net 두 줄을 나란히 찍는다: 지속되는
> `feedback` 레코드의 `expectancy`는 여전히 gross이고, 그것을 바꾸는 것은 별도 증분이다.
> **본문은 검토 시점 그대로 두었다** — 무엇을 근거로 그 순서를 골랐는지가 기록의 값이다.

이 문서는 런타임 권한을 부여하지 않는다. 거버넌스/실행 경로가 아니라 **전략 자체의 통계적·
경제적 타당성**을 본 기록이다. 이미 `docs/REMAINING_WORK.md`에 적힌 항목은 중복 제외했고,
거기에 없는 것 위주로 적었다. 각 항목은 `파일:줄` 근거를 단다.

먼저 공정하게: 파이프라인 위생 상태는 이 규모의 개인 프로젝트로서는 매우 좋다. 평가기가
fail-closed(`strategy.py:392`), HTF 정렬이 close_time 키라 구조적으로 look-ahead 불가
(`features.py:160`), 백테스트와 라이브가 **같은** evaluator/settlement을 공유(`factory.py:5`),
비용 기준(cost basis)과 증거 깊이(evidence depth)가 랭킹 tier로 승격되어 있음(`pool.py:1102`).
아래는 그 위에서 남은 문제들이다.

---

## 요약 — 우선순위

| # | 항목 | 심각도 | 성격 |
|---|---|---|---|
| A1 | 다중검정(multiple testing) 보정 부재 — 수백 후보 중 최대값 선택 | **치명** | 통계 |
| A2 | 홀드아웃이 후보 전체에 공유·재사용되어 사실상 in-sample화 | **치명** | 통계 |
| A3 | `MIN_HOLDOUT_TRADES = 3`, `total_R > 0`만으로 CONFIRMED | **치명** | 통계 |
| A4 | `free_parameters`가 탐색 자유도를 과소 계상 | 높음 | 통계 |
| A5 | walk-forward가 진짜 walk-forward가 아님 / `temporal_stability` 영구 None | 높음 | 통계 |
| A6 | regime_breadth에 regime당 최소 표본 없음 | 중간 | 통계 |
| B1 | **펀딩비가 어디에도 반영되지 않음** (무기한 선물, 최대 48시간 보유) | **치명** | 비용 |
| B2 | Paper R은 비용 0인데 lifecycle 강등 임계치가 그 위에서 판정 | **치명** | 비용 |
| B3 | 슬리피지 3bps 상수 — 심볼/변동성/사이즈 무관, 미측정 | 높음 | 비용 |
| B4 | TP maker 체결 가정에 부분/미체결 모델 없음 | 높음 | 비용 |
| C1 | 계획가 대비 실제 체결가 편차 가드 없음 + 스톱/타깃 재계산 없음 | **치명** | 정합성 |
| C2 | R 기준 3종(backtest net / paper intent / live gross)이 혼용 비교됨 | 높음 | 정합성 |
| D1 | 청산 모델이 ATR 고정 브래킷 하나뿐 | 높음 | 표현력 |
| D2 | 진입 규칙이 단일 봉 스냅샷 — 교차/지속/시퀀스 표현 불가 | 높음 | 표현력 |
| D3 | 포지션 사이징 고정 1% — 신뢰도/변동성 기반 조절 없음 | 중간 | 표현력 |
| D4 | `LONG_SHORT` 방향이 항상 LONG 진입 | 낮음 | 표현력 |
| E1 | 파라미터 탐색이 base 주변 ±35% jitter뿐 | 높음 | 탐색 |
| E2 | fusion이 AND-union만 → 거래수 단조 감소, 표본 적정성과 충돌 | 중간 | 탐색 |
| E3 | 템플릿 20종이 단일 계열(방향성 TA)에 편중 | 중간 | 탐색 |
| F1 | **상관관계 통제 전무** — paper 동시 20포지션 × 1%R | **치명** | 포트폴리오 |
| F2 | 리스크 가드가 사후 회로차단기뿐, 사전 노출 한도 없음 | 높음 | 포트폴리오 |
| F3 | 드로다운 한도가 R 프록시(10R) — 실제 equity 곡선 아님 | 중간 | 포트폴리오 |
| G1 | OI가 일봉 해상도인데 1h 프레임에 정렬 | 중간 | 데이터 |
| G2 | mark/index/basis가 close fallback → basis 항상 0 | 중간 | 데이터 |
| G3 | liquidation feature 3종이 계산되지만 mint 불가 | 낮음 | 데이터 |
| G4 | `liquidation_spike_ratio`만 피드 없을 때 상수 0.0 fallback | 중간 | 데이터 |
| H1 | 라우팅 승자 선택이 `champion_score`(=견고성)이지 수익성이 아님 | 중간 | 운영 |
| H2 | 후보 스토어 무한 성장 — 만료/가지치기 없음 | 중간 | 운영 |
| H3 | lifecycle 강등이 rolling 20/30/50 거래 필요 — 실시간으로는 수개월 | 중간 | 운영 |

---

## A. 통계적 타당성 — "이 엣지가 진짜인가"를 판정하는 층

이 층이 이 시스템에서 가장 약하다. `robustness.py`는 과최적화를 **점수화**하지만
**검정**하지는 않는다.

### A1. 다중검정 보정이 없다 (치명)

`factory.run_factory`는 매 스케줄 실행마다 후보를 mint하고
(`factory.py:1093`), `pool.rank_candidates`는 누적 스토어 전체에서 최대값을 뽑는다
(`pool.py:1102`). 코드 주석이 스스로 밝히듯 이 머신에 이미 **359개 행 / 269개 후보**가 쌓여
있다(`pool.py:1016`, `pool.py:1114`).

문제는 그 359개가 **거의 같은 데이터 위에서** 채점됐다는 점이다. 리플레이 창은 롤링 500일
(`market_data.py:109`)이므로 하루 간격으로 mint된 두 세대는 창의 99.8%가 겹친다. 같은 표본에서
N번 뽑아 최대값을 취하면 기대 최대 통계량은 N과 함께 자란다 — 순수 노이즈여도 그렇다.

현재 방어책은 홀드아웃 하나뿐인데, A2/A3에서 보듯 그것도 새지 않는다. 없는 것:

- 후보 수를 반영한 유의성 보정 (Bonferroni / FDR / White's Reality Check / SPA)
- expectancy의 t-통계량 또는 부트스트랩 신뢰구간
- 순열검정(permutation test) — 라벨을 섞었을 때 이 점수가 얼마나 자주 나오는지
- Deflated Sharpe Ratio 계열의 "시도 횟수"를 아는 지표

`grep -rn "p_value\|bootstrap\|permutation\|sharpe\|significance" runtime/mvp_runtime/crypto/`
→ 0건. `robustness_score`는 5개 항의 가중합(`robustness.py:72`)이고, 그 어느 항도 "몇 번
시도했는가"를 모른다.

**최소 개선:** 후보 레코드에 `attempts_in_lineage`(그 창에서 채점된 총 후보 수)를 기록하고,
`expectancy`의 표준오차(`std(R)/sqrt(n)`)를 evidence에 실어 랭킹에 t = expectancy/SE를 tier로
넣는 것. 이건 새 스키마 없이 `backtest_spec` 반환 딕셔너리 확장만으로 가능하다.

### A2. 홀드아웃이 후보 전체에 공유되고 재사용된다 (치명)

`holdout_split_index`는 **최근 30%**를 잘라낸다(`factory.py:601`, `HOLDOUT_FRACTION = 0.30`).
이건 후보별로 다른 구간이 아니라 **모든 후보가 같은 최근 꼬리**를 본다는 뜻이다. 게다가 매일
재실행되므로 어제의 홀드아웃 꼬리는 오늘도 (하루 밀린 채) 홀드아웃이다.

홀드아웃의 통계적 힘은 "한 번만 본다"에서 나온다. 269개 후보가 같은 꼬리에서 확인받고, 그중
통과한 것만 랭킹 상위에 남으면 그 꼬리는 사실상 학습 집합이다. 모듈 docstring이
"promotion then selected the maximum of many such scores, which is how noise gets promoted"
(`robustness.py:63`)라고 정확히 지적한 그 실패 모드가, 홀드아웃 층에서 한 단계 늦게 재현된다.

**최소 개선:** 홀드아웃 소진(budget)을 추적할 것 — 한 (symbol, timeframe, 창) 조합에서 홀드아웃
확인을 받은 후보 수를 세고, 임계치를 넘으면 `HOLDOUT_EXHAUSTED` 상태를 verdict에 반영. 또는
후보별로 결정론적이지만 서로 다른 구간을 홀드아웃으로 배정(예: rule_hash로 블록 선택)해서
공유를 끊는 것.

### A3. 홀드아웃 통과 기준이 너무 약하다 (치명)

```python
MIN_HOLDOUT_TRADES = 3                                    # robustness.py:70
return HOLDOUT_CONFIRMED if _f(holdout.get("total_R")) > 0 else HOLDOUT_CONTRADICTED
```
(`robustness.py:180`)

거래 3건에 총 R이 양수면 CONFIRMED고, CONFIRMED는 ROBUST 등급의 **유일한** 관문이다
(`robustness.py:193`). 설계 R:R이 1.0~8.0 사이(`factory.py:85`, 기본 2.5배)인 코인 던지기
전략도 3거래 중 1승만 하면 총 R이 양수가 된다 — 확률로 대략 40~50%다. 269개 후보 중 100개
이상이 순수 우연으로 CONFIRMED를 받을 수 있다.

**최소 개선:** `MIN_HOLDOUT_TRADES`를 20~30 수준으로 올리고, 기준을 `total_R > 0`이 아니라
"홀드아웃 expectancy가 in-sample expectancy의 신뢰구간 안에 있는가"로 바꾸는 것. 표본이 부족하면
INSUFFICIENT로 떨어지는 현재 경로가 이미 있으므로(`robustness.py:68`) 구조 변경은 없다.

### A4. 자유도 계산이 탐색 공간을 과소 계상한다

```python
literals = sum(1 for c in spec.entry_rules.conditions if c.value is not None)
return literals + EXIT_FREE_PARAMETERS   # robustness.py:84
```

세는 것: 진입 조건의 리터럴 임계값 + 청산 파라미터 3개. 세지 **않는** 것:

- 20개 템플릿 패밀리 중 어느 것을 골랐는가 (`factory.py:345`)
- 4개 timeframe 중 어느 것인가 (`strategy.py:36`)
- 어느 심볼인가
- 그 조합으로 몇 번 시도했는가 (A1)

`trades_per_parameter`가 `sample_adequacy`(가중치 0.30, 최대 항)와 FRAGILE veto
(`robustness.py:186`)를 동시에 지배하는데, 분모가 실제보다 작으므로 두 판정 모두 낙관 쪽으로
치우친다. 예: `htf_pullback_long`은 리터럴이 1개뿐이라 free_parameters = 4, 거래 20건이면
trades_per_parameter = 5.0으로 간신히 critical을 넘는다. 실제 자유도(패밀리 선택 20 × timeframe
4 × 리트라이 12회, `factory.py:81`)를 감안하면 5.0은 사실이 아니다.

### A5. walk-forward가 walk-forward가 아니다

```python
window_bars = max(1, len(rows) // BACKTEST_WINDOWS)      # factory.py:762, BACKTEST_WINDOWS = 3
walk_forward = {"walk_forward_pass_rate": ..., "temporal_stability": None, ...}
```

같은 파라미터로 리플레이한 구간을 3등분해 각 구간의 **부호**만 세는 것이다. 진짜 walk-forward
(구간마다 재적합 후 다음 구간에서 검증)가 아니다. 구간당 최소 3거래(`MIN_TRADES_PER_WINDOW = 3`)면
카운트에 들어가므로, 3거래 구간 3개 = 9거래로 pass_rate 1.0이 나올 수 있다.

`temporal_stability`는 영구히 `None`이고(`factory.py:773`), `_temporal_consistency`는 존재하는
항만 평균한다(`robustness.py:96`). 즉 **가중치 0.25짜리 항이 절반만 측정된 값으로 채점되고**,
`insufficient_walk_forward_evidence` 경고가 모든 후보에 붙는다(`robustness.py:16`에 명시). 모든
후보에 동일하게 붙는 경고는 랭킹에 정보를 주지 않는다.

### A6. regime breadth에 regime당 최소 표본이 없다

```python
return _clamp01(profitable / len(traded))   # robustness.py:114
```

`regimes_traded`는 진입 시점 regime의 **집합**이고, regime당 거래 1건이어도 한 표로 센다
(`factory.py:750`). TREND_UP 30거래 손실 + RANGE 1거래 이익 = breadth 0.5. 가중치 0.20이
이렇게 소비된다.

---

## B. 비용 모델의 현실성

### B1. 펀딩비가 어디에도 반영되지 않는다 (치명)

```
grep -rn "funding" runtime/mvp_runtime/crypto/cost.py → 0건
```

`CostModel`은 taker / maker / slippage 세 개뿐이다(`cost.py:80`). 그런데 거래 대상은 **USD-M
무기한 선물**이고, `max_holding_bars`는 12~48(`factory.py:163`)이므로 1h 프레임에서 최대 48시간
보유다. 펀딩은 8시간마다 정산되므로 **한 포지션이 1~6회 펀딩을 낸다**.

규모 감각: 스톱 거리가 `stop_atr × ATR` ≈ 1.2 × (가격의 0.4%) ≈ 0.5%다. 즉 1R ≈ 명목의 0.5%.
평시 펀딩 0.01%/8h이면 하루 0.03% = **0.06R/일**. 이틀 보유하면 0.12R이 사라진다. 후보들의
expectancy가 통상 0.05~0.20R 범위인 걸 감안하면 **엣지 전체를 삼킬 수 있는 크기**다.

더 나쁜 건 모순이다: `funding_fade_long` / `funding_fade_short` 패밀리는(`factory.py:328`)
**펀딩이 극단일 때 진입한다**. 펀딩 z-score가 ±1.5σ인 지점은 펀딩 절대값이 가장 큰 지점이고,
short 진입이면 펀딩을 받지만 long 진입이면 가장 비싼 값을 낸다. 펀딩을 모델링하지 않은 채로
펀딩 극단에서 진입하는 전략을 채점하고 있다.

**최소 개선:** `apply_cost_model`에 `holding_hours`와 `avg_funding_rate`를 받아
`funding_cost_r = funding_rate × holding_periods × entry_price / risk`를 빼는 것. 데이터는 이미
있다 — `features.py:245`가 `funding_rate` 시계열을 정렬해 두고, `_replay`는 진입/청산 봉 인덱스를
안다(`factory.py:656`). `CostBreakdown`에 필드 하나 추가 + `cost_summary.cost_model`에 기록
(기존 비용 기준 tier 메커니즘이 그대로 옛 후보를 `optimistic`으로 강등시켜 줌).

### B2. Paper R은 비용 0인데 강등 판정이 그 위에서 이뤄진다 (치명)

설계상 명시된 경계다: "live paper R stays cost-free by design"(`cost.py:28`),
`r_basis: R_BASIS_INTENT`(`paper.py:537`). 백테스트만 비용을 적용한다.

그런데 `lifecycle.evaluate_lifecycle`의 강등 임계치는:

```python
warn_expectancy_r: float = 0.0
probation_expectancy_r: float = -0.05
suspend_expectancy_r: float = 0.0        # lifecycle.py:113-124
```

이 `expectancy_r`는 paper outcome의 `result_R` 평균이다 — **비용 0 기준**. 즉 gross로 +0.02R,
net으로 -0.08R인 전략(수수료 왕복 10bps + 슬리피지 6bps + 펀딩이면 흔한 값)은 **영원히
PAPER_ACTIVE로 남는다**. 강등 시스템이 실제로 돈을 잃는 전략을 잡아내지 못한다.

동시에 `live_vs_backtest_win_rate_drop`(`lifecycle.py:100`)은 net 기준 백테스트와 cost-free
paper를 비교한다. 승률은 비용에 상대적으로 둔감하니 이 특정 비교는 덜 왜곡되지만, 두 계열의
기준이 다르다는 사실 자체가 문서 밖에 없다.

**최소 개선:** paper 정산에도 `apply_cost_model`을 적용하고 `result_R_net`을 별도 필드로 실을 것
(기존 `result_R`은 보존 — 과거 레코드 재작성 금지 원칙 유지). lifecycle과 risk guard가 net을
읽게 하면 임계치 숫자는 그대로 두고 의미만 정상화된다.

### B3. 슬리피지가 미측정 상수다

```python
DEFAULT_SLIPPAGE_BPS = 3.0   # cost.py:56
```

주석이 정직하게 인정한다: "nothing here has measured it — a canary is a single market order,
not a sample." 문제는 3bps가 심볼(BTC vs SOL), 변동성 국면(HIGH_VOLATILITY regime에서 진입하는
전략이 있다), 주문 크기와 무관하게 고정이라는 점이다. taker 수수료는 실측해서 2.5 → 5.0bps로
고쳤는데(`cost.py:37`) 슬리피지는 그 절반 크기의 항인데도 손대지 않았다.

`live_execution`은 이미 fill의 `avg_price`를 갖고 있다(`live_leg.py:394`). 의도가(마지막 종가)
대비 실제 체결가의 차이를 outcome 레코드에 적재하면 몇 십 건만으로 실측 분포가 생긴다.

### B4. TP를 maker 체결로 가정하는데 미체결 모델이 없다

2026-07-28 변경으로 타깃이 resting LIMIT이 됐고(`cost.py:60`), 백테스트는 "타깃 터치 = 전량
maker 체결"로 정산한다(`paper.py:488`). 실제로는:

- 가격이 타깃을 **스치고** 반전하면 큐에서 부분 체결되거나 아예 안 된다
- resting LIMIT은 큐 우선순위가 있어서 터치만으로 체결이 보장되지 않는다
- 반면 백테스트는 `high >= tp`면 무조건 tp 가격 전량 체결로 처리한다

이 방향의 오차는 **낙관 쪽**이다 — 이긴 거래는 다 잡고 진 거래는 시장가로 확실히 나간다는 가정.
`maker_fee_bps = 2.0`도 published rate이고 미측정이라 오차 방향이 위험한 쪽임을 코드가 명시한다
(`cost.py:66`).

**최소 개선:** 타깃 터치 시 `high > tp × (1 + ε)`처럼 관통(penetration)을 요구하는 보수적 규칙,
또는 터치-만-한 경우를 별도 `close_reason`으로 분리해 통계를 나눠 보는 것.

---

## C. 신호–체결 정합성

### C1. 계획가 대비 실제 체결가 편차 가드가 없다 (치명)

경로를 따라가면:

1. `build_entry_plan`이 **마지막 종가**를 `entry_price`로 잡고 ATR로 스톱/타깃을 계산
   (`paper.py:243`, `paper.py:256`)
2. `price_bracket`이 그 `plan["entry_price"]` 기준으로 스톱/타깃을 틱 라운딩하고
   `risk_per_unit = |plan_entry − stop|`을 확정(`live_entry.py:87`, `live_entry.py:115`)
3. `size_live_order`가 그 `risk_per_unit`으로 수량을 계산(`live_sizing.py:186`)
4. 실제 진입은 **MARKET 주문**(`live_leg.py:383`)
5. 포지션은 **실제 체결가**로 기록(`live_position.py:117`) — 그런데 스톱/타깃은 2단계에서
   확정된 **stale 가격 기준 값을 그대로** 사용(`live_leg.py:437`, `live_leg.py:471`)

`grep -rn "deviation\|max_slip\|price_drift" live_entry.py live_leg.py live_order.py` → 가드 없음.

결과: 체결가가 계획가에서 벗어나면 실현 R:R이 설계와 달라진다. 롱에서 0.2% 불리하게 체결되면
스톱까지 거리는 0.5% → 0.3%로 줄고(리스크 40% 증가, 사이징은 이미 확정), 타깃까지는 늘어난다.
설계 R:R 2.5가 실제로는 1.5가 된다. 스케줄러는 최대 15분 간격으로 도는데(`scheduler.py`),
1h 봉 종가 이후 최대 15분 지연이면 변동성 국면에서 0.3% 이동은 흔하다 — **스톱 거리의 절반**이다.

**최소 개선(둘 중 하나, 둘 다면 더 좋음):**
- 진입 직전 mark price를 읽어 `|mark − plan_entry| / risk_per_unit > 0.25`면 `STALE_SIGNAL`로 거부
- 체결 후 실제 fill 기준으로 스톱/타깃을 **재계산**해서 브래킷을 걸기 (설계 R:R 보존).
  단 이 경우 리스크 금액이 변하므로 사이징도 fill 기준으로 재검증해야 함 — 현재 구조에서는
  거부 쪽이 더 단순하고 fail-closed 원칙에도 맞는다.

### C2. R 기준 3종이 혼용 비교된다

| 계열 | 기준 | 근거 |
|---|---|---|
| backtest | net (taker/maker/slippage 차감, 펀딩 제외) | `factory.py:781` |
| paper | intent — 비용 0 | `paper.py:537`, `cost.py:28` |
| live | 거래소 실체결, **fees·funding gross** | `live_leg.py:58` |

`lifecycle.compute_strategy_performance`는 백테스트와 실현 계열을 나란히 놓고
`live_vs_backtest_win_rate_drop`을 계산하고(`lifecycle.py:100`), `guards.run_risk_guard`는
`result_R`을 기준으로 일/주 손실 한도를 잰다(`guards.py:210`). `paper.py`는 `r_basis` 필드를
행에 실어 두었지만(`paper.py:537`) **읽는 쪽이 아무도 없다** — 즉 기준 표시는 있으나 강제되지
않는다.

**최소 개선:** `r_basis`가 다른 행을 같은 통계에 합산하려 하면 거부하거나, 최소한 요약 레코드에
`mixed_r_basis: true`를 남기는 것. 이 저장소의 `cost_basis_rank` 패턴(`pool.py:190`)이 이미
같은 문제를 랭킹에서 푼 선례다.

---

## D. 전략 표현력 — `strategy_spec.v1` 스키마의 상한

여기가 "성능 상한"을 정하는 층이다. 현재 스키마로 표현 **불가능한** 전략은 아무리 탐색해도
나오지 않는다.

### D1. 청산 모델이 ATR 고정 브래킷 하나뿐

```python
ALLOWED_STOP_MODELS = frozenset({"atr"})   # strategy.py:37
```

`ExitRules`는 `stop_atr`, `target_atr`, `max_holding_bars` 세 숫자뿐이다(`strategy.py:152`).
없는 것: 트레일링 스톱, 본전 이동(breakeven), 분할 익절(scale-out), 변동성 변화에 따른 스톱 조정,
시간 경과에 따른 타깃 축소, 반대 신호 청산, regime 변화 청산.

실무적으로 **청산이 기대값의 가장 큰 결정 요인**인데, 이 시스템의 탐색은 청산 쪽에서 3개 스칼라만
움직인다. 진입 조건은 20개 패밀리로 다양화하면서 청산은 단일 형태다 — 비대칭이 심하다.

특히 시간 청산이 `last_close`로 정산되는데(`paper.py:493`), 이건 "48시간 지나면 시장가 청산"이다.
추세가 살아 있어도 자른다.

### D2. 진입 규칙이 단일 봉 스냅샷이다

`RuleCondition`은 `feature (op) value | value_from`이고, `value_from`은 **같은 행의** 다른 컬럼만
참조한다(`strategy.py:398`). lag 연산자가 없다. 따라서 표현 불가능한 것:

- 교차(crossover): "MACD가 시그널을 **상향 돌파한 봉**" — 현재는 `macd > macd_signal`이라는
  **상태**만 표현되고, 그 상태는 추세 내내 참이므로 매 봉 재진입 신호가 된다
- 지속: "ADX가 5봉 연속 상승"
- 시퀀스: "고점 갱신 후 되돌림"
- 상대: "20봉 전 대비" (일부는 `roc_4`가 커버하지만 고정 기간 하나뿐)

이건 스키마 확장으로 풀 수 있다 — `feature`에 `lag: n`을 허용하고 `features.build_feature_rows`가
`prev_*` 컬럼을 만드는 정도. 다만 자유도가 늘어나므로 A4와 함께 처리해야 한다.

### D3. 포지션 사이징이 고정 1%

```python
RISK_PER_TRADE_FRACTION = 0.01   # live_sizing.py:49
RISK_PER_TRADE = 0.01            # guards.py:41
```

`risk_constraints.max_risk_per_trade_R`이 스펙에 있지만(`strategy.py:201`) 라이브 사이징은 이걸
읽지 않고 상수를 쓴다(`live_sizing.py:136`). 신뢰도(robustness verdict), 최근 성과, 변동성 국면에
따른 사이징 조절이 없다. 변동성 타깃팅도, 켈리 기반 비율도, 전략별 자본 배분도 없다 — 모든 전략이
동일 리스크를 받는다.

### D4. `LONG_SHORT` 방향이 항상 LONG으로 진입한다

```python
direction = "SHORT" if spec.direction is Direction.SHORT else "LONG"   # strategy.py:437
```

docstring이 "LONG_SHORT until directional rule sets exist, both enter long"이라고 인정한다.
팩토리는 long/short만 mint하므로 현재는 무해하지만, `Direction.LONG_SHORT`가 enum에 존재하고
`from_dict`가 이를 받아들이므로 **임포트된 스펙이나 proposer 제안이 조용히 롱 전용이 된다**.
차라리 파싱 단계에서 거부하는 게 fail-closed 원칙에 맞다.

---

## E. 탐색 전략의 한계

### E1. 파라미터 탐색이 base 주변 jitter뿐

```python
span = (spec.hi - spec.lo) * scale          # _MUTATION_SCALE = 0.35
val = base + rng.uniform(-span, span)       # factory.py:493
```

`base`는 항상 템플릿의 고정 `base_params`다(`factory.py:348` 등). 즉 세대가 100번 지나도
**같은 중심 주변 ±35%**만 뽑는다. 성과가 좋았던 파라미터 쪽으로 중심이 이동하지 않는다 —
진화가 아니라 반복 표본추출이다.

`known_rule_hashes` 중복 가드가 있으므로(`factory.py:580`) 완전 동일 스펙은 안 나오지만,
`near_duplicate_groups`가 따로 존재한다는 사실 자체가(`pool.py:632`) 근접 중복이 실제로 쌓이고
있음을 말해준다.

**최소 개선:** 상위 후보의 파라미터를 다음 세대의 `base_params`로 쓰는 것(간단한 (1+λ)-ES).
결정론은 유지된다 — 시드가 아니라 base가 증거에서 파생되므로 재현 가능하다.

### E2. fusion이 AND-union만이라 거래수가 단조 감소한다

```python
"""Cross two parents into a child that enters only where BOTH would."""   # factory.py:859
```

자식은 **구조적으로 두 부모보다 항상 더 selective**하다. 그런데 `robustness`의 지배 항은
`trades_per_parameter`이고(가중 0.30 + FRAGILE veto), 조건이 늘면 free_parameters도 늘어난다
(`robustness.py:84`). 즉 fusion은 분자를 줄이고 분모를 늘린다 — **점수를 떨어뜨리는 방향으로만
작동하는 연산자**다. `no_trades` 거부 경로가 있다는 것(`factory.py:1056`)이 이미 이 압력을 보여준다.

OR 결합, 조건 교체(uniform crossover), 조건 삭제 같은 **완화 방향** 연산자가 없다.

### E3. 템플릿이 단일 계열에 편중

20개 패밀리(`factory.py:345`)는 전부 "단일 심볼 · 단일 타임프레임 · 방향성 TA/파생지표"다.
없는 계열:

- **횡단면(cross-sectional)**: 상대강도 기반 롱숏 — 코인 간 비교
- **시간 계절성**: 요일/시간대 효과 (`timestamp`가 행에 있지만 feature vocabulary에 없음)
- **베이시스/스프레드**: mark-index basis가 항상 0이라 불가 (G2)
- **마켓 마이크로구조**: 호가/체결 불균형 — 데이터 자체가 없음
- **평균회귀 페어**: 심볼 스코프가 사실상 단일 심볼(`factory.py:511`, `symbol_scope: [symbol]`)

`proposer.py`(LLM 패밀리 제안)와 `data_review.py`(데이터 갭 리뷰)가 정확히 이 문제를 겨냥해 만들어져
있는 건 좋은 설계다 — 다만 둘 다 제안만 하고 실제 확장은 사람 코드 변경이다.

---

## F. 포트폴리오 · 리스크

### F1. 상관관계 통제가 전혀 없다 (치명)

```
grep -rn "correlat" runtime/mvp_runtime/crypto/ → 0건
```

Paper 한도:
```python
MAX_CONCURRENT_POSITIONS = 20      # paper.py:113  (5 symbols × 4 timeframes)
MAX_POSITIONS_PER_SYMBOL = 4       # paper.py:114
```

각 포지션이 1R씩이면 **동시 20R 노출**이다. 그런데 대상은 BTC/ETH/SOL 등 USD-M 무기한이고,
이들의 일간 수익률 상관은 통상 0.8~0.95다. 즉 20개 독립 베팅이 아니라 **사실상 하나의 베팅을
20배로 건 것**에 가깝다. 급락 한 번에 20R이 동시에 스톱될 수 있고, 이는 일일 한도 -2R
(`guards.py:42`)를 10배 초과한다 — 한도는 **이미 다 맞고 나서** 다음 진입을 막을 뿐이다.

또한 한 심볼에 4개 타임프레임 포지션은 상관 1.0에 가깝다. 같은 방향이면 그냥 4배 사이즈다.

라이브는 `MAX_LIVE_CONCURRENT_POSITIONS = 2`, `MAX_LIVE_POSITIONS_PER_SYMBOL = 1`
(`live_position.py:68`)로 훨씬 보수적이라 **당장의 실금 위험은 낮다**. 하지만 문제는 두 가지다:
(a) paper 트랙레코드가 이 상관 무시 상태에서 쌓이고 있어서 그 통계가 라이브 확대 시의 근거로
쓰일 수 없고, (b) 라이브 한도를 올리는 순간 통제 수단이 아무것도 없다.

**최소 개선:** 진입 전에 "이미 열린 포지션들과 같은 방향인가"를 세고 방향별 합산 R 한도를 두는 것.
정교한 상관 행렬 없이 `same_direction_open_risk_r > N`이면 거부하는 것만으로 최악의 경우를 막는다.

### F2. 리스크 가드가 사후 회로차단기뿐이다

`run_risk_guard`가 보는 것(`guards.py:203`)은 전부 **이미 닫힌** 거래다: 일 손실, 주 손실, 연속
손실, 드로다운. 열린 포지션의 미실현 노출은 어디에도 안 들어간다. `allow_new_position`은
"과거에 얼마나 잃었나"만 답한다.

즉 20개 포지션이 동시에 열려 있고 아직 아무것도 안 닫혔으면, 가드는 "정상"이라고 답한다.

### F3. 드로다운 한도가 R 프록시다

```python
return (abs(MAX_DRAWDOWN_PCT) / 100.0) / risk_pct   # guards.py:178 → 10R
```

-10% equity를 1% risk로 나눠 10R로 환산한다. 이건 **모든 거래가 정확히 1% 리스크로 체결됐다는
가정** 위에서만 맞다. 실제로는 사이징이 `min(risk-based, budget cap)`이라 budget에 묶이면
(`live_sizing.py:185`, `bound_by = "budget"`) 실제 리스크가 1%보다 작다. 그러면 10R 한도는 실제
-10%보다 훨씬 관대해진다. 계좌 스냅샷은 이미 읽고 있으므로(`account.py:340`) 실제 equity 곡선으로
재는 편이 정확하다.

또 이 한도들은 **레버리지·마진·청산가를 전혀 고려하지 않는다**. `account.py:127`에 `leverage`
필드가 있지만 리스크 판정에 쓰이지 않는다.

---

## G. 데이터 품질

### G1. OI가 일봉 해상도인데 1h 프레임에 쓰인다

주석이 정직하게 기록한다(`features.py:279`): 정렬된 시계열은 1h 봉의 4.2%에서만 값이 변하고,
squeeze 조건은 12,000봉 중 0회 교차했다. 그래서 이벤트 시간축에서 파생을 계산한 뒤 정렬하도록
고쳤다 — 좋은 수정이다. 하지만 **정보 자체가 하루 1개**라는 사실은 그대로다. `oi_squeeze_*` /
`oi_unwind_*` 4개 패밀리가 1h에서 mint되면, 그 진입 조건 중 OI 항은 하루 종일 같은 값이다.
사실상 "그날 OI가 3% 늘었으면 그날 하루 종일 진입 허용"이라는 일간 필터다.

`oi_store.py`에 `1hour` interval 지원이 있으나(`market_data.py:73`) 벤더 이력이 ~84일뿐이라
500일 리플레이를 못 채운다. 즉 백테스트는 일간으로, 라이브는 시간별로 볼 수 있다면 **기준이
달라진다** — 확인 필요.

### G2. basis가 항상 0이다

```python
"mark_price": close, "index_price": close, "mark_index_basis_bps": 0.0,   # features.py:353
```

소스 시스템의 동작을 그대로 옮긴 것이고 주석도 명시하지만, 결과적으로 `mark_index_basis_bps`는
**상수 0**이다. 그런데 이 이름은 `NUMERIC_FEATURES`에 들어 있어(`factory.py:100`) 팩토리가
`mark_index_basis_bps > 0.5` 같은 조건을 mint할 수 있고, 그건 **영원히 거짓**이다 — 즉 그 스펙은
절대 거래하지 않는다. `mark_price`/`index_price`도 close와 동일하므로 이들 간 비교 조건도 무의미하다.

G4와 같은 부류지만 반대 방향이다(항상 거짓 vs 항상 참). 세 이름을 vocabulary에서 빼는 게 맞다.

### G3. liquidation feature 3종이 계산되지만 mint 불가

`proposer.py:36`이 이미 기록: `liquidation_total`, `long_liquidation`, `short_liquidation`은
feature row에 계산되지만 validator vocabulary에 없어 제안이 거부된다. 그런데
`factory.py:116`을 보면 `NUMERIC_FEATURES`에 **이미 추가되어 있다**. 즉 proposer의 주석이
스테일이거나, 두 경로가 실제로 다르게 동작한다. 어느 쪽이든 문서와 코드가 어긋나 있다.
(팩토리는 mint 가능, proposer 문서는 불가능이라고 안내 — 사용자에게 잘못된 안내가 나간다.)

### G4. `liquidation_spike_ratio`만 피드 없을 때 상수 0.0

`factory.py:111`이 이 위험을 명시적으로 기록한다: 다른 feature는 피드가 없으면 `None`이라
평가 불가 → 진입 없음이지만, `liquidation_spike_ratio`만 legacy 상수 0.0으로 채워진다
(`features.py:267`). 따라서 `spike_ratio < 1.5` 같은 조건은 **피드가 죽어도 참**이 된다.
코드가 "이 hazard는 이 변경으로 확대되지 않는다"고만 하고 닫지는 않았다 — 닫는 게 맞다.
`None`으로 바꾸면 그 조건을 쓰는 스펙이 피드 없이는 거래하지 않게 된다(fail-closed 원칙 일치).

---

## H. 라이프사이클 · 운영

### H1. 라우팅 승자를 견고성 점수로 고른다

```python
key=lambda m: (m["champion_score"] ...)   # paper.py:222
```

`champion_score`는 `robustness_score`다(`factory.py:823`). 즉 여러 전략이 동시에 매치하면
**가장 과최적화가 덜 된 것**이 이기고, 가장 수익성 높은 것이 이기는 게 아니다.

견고성을 **문지기**로 쓰는 건 옳다(FRAGILE은 승격 자체를 막아야 함). 하지만 이미 풀에 들어온
전략들 사이의 **선택**에서까지 견고성이 유일 기준이면, expectancy 0.30R짜리가 0.05R짜리에게
robustness 0.72 vs 0.75로 진다. `pool.candidate_quality`가 `edge_quality`(승률 × R:R)를 이미
계산해 두었으므로(`pool.py:1063`), 라우팅에서도 (verdict tier, edge_quality) 순서를 쓰는 게
일관적이다.

### H2. 후보 스토어가 무한 성장한다

359행 누적(`pool.py:1114`), 만료·가지치기 경로 없음. `read_candidates`는 전부 읽고
(`pool.py:906`), `rank_candidates`는 전부 정렬한다. 문제는 성능이 아니라 **통계다** — A1에서
말했듯 스토어가 클수록 최대값 선택 편향이 커진다. 오래된 비용 기준(`optimistic`)이나 얕은 증거
깊이는 tier로 강등되지만 **제거되지는 않는다**.

### H3. 강등이 실시간으로는 수개월 걸린다

`LifecycleThresholds`는 rolling 20 / 30 / 50 거래, ARCHIVE는 lifetime 100거래를 요구한다
(`lifecycle.py:113-129`). 1h 전략이 진입 조건 매치율 ~5%라면 하루 1.2회, 거래당 평균 보유 24시간
이면 **rolling 50에 도달하는 데 1~2개월**이다. `SUSPENDED`까지는 그보다 더 걸린다.

즉 실제로 손실 중인 전략이 두 달간 라우팅 슬롯을 점유한다. `route_entries`는 컨텍스트당
**하나**만 고르므로(`lifecycle.py:238` 주석이 정확히 이 문제를 지적) 슬롯 점유 비용이 크다.
`operator_retirement_decision`이 이 때문에 추가됐지만(`lifecycle.py:247`) 그건 수동 경로다.

**최소 개선:** 절대 거래수 외에 "누적 -XR" 같은 빠른 회로차단기를 전략 단위로 하나 더 두는 것.
포트폴리오 단위 가드는 있는데(`guards.py`) 전략 단위 즉시 차단기는 없다.

---

### H4. 사소한 것 — `_replay`의 반환 타입 주석이 스테일

```python
) -> tuple[list[dict[str, Any]], float, float]:          # factory.py:617 — 3-tuple
    ...
    return outcomes, total_fee_cost_r, total_maker_fee_cost_r, total_slippage_cost_r   # :678 — 4-tuple
```

호출부 두 곳 모두 4개로 언패킹하므로(`factory.py:690`, `factory.py:741`) 런타임 문제는 없다.
maker fee 항이 추가될 때 주석만 안 따라온 것. 타입 체커를 붙이면 바로 잡힌다.

---

## 권장 처리 순서

기존 아키텍처를 흔들지 않고, 각각 독립적으로 머지 가능한 순서:

1. **B1 펀딩비** — `CostBreakdown`에 필드 하나, `_replay`에 보유시간 전달. 데이터는 이미 있음.
   비용 기준 tier가 자동으로 옛 후보를 강등시켜 줌. 가장 큰 효과 대비 가장 작은 변경.
2. **C1 체결 편차 가드** — `plan_live_entry`에 거부 사유 하나 추가. 실금 경로 보호.
3. **A3 홀드아웃 기준 강화** — 상수 두 개(`MIN_HOLDOUT_TRADES`, 판정식). 구조 변경 없음.
4. **B2 paper 비용 반영** — `result_R_net` 추가 + lifecycle/guard가 net을 읽게.
5. **F1 방향별 노출 한도** — paper 한도에 조건 하나. 라이브 확대의 전제 조건.
6. **A1 유의성** — evidence에 `stderr`/`t_stat`/`attempts` 기록, 랭킹 tier 추가.
7. **G2/G4 vocabulary 정리** — 항상 참/항상 거짓인 feature 제거 또는 None화.
8. **D1 청산 모델 확장** — 스키마 변경이라 가장 큼. 앞의 것들이 끝난 뒤.

A1·A2를 제대로 풀려면 결국 "이 창에서 몇 번 뽑았는가"를 후보 레코드가 알아야 한다. 그게
이 시스템에서 가장 부족한 단일 정보다.
