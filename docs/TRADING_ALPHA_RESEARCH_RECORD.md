# 수익률 개선 리서치 — 전략 방향과 데이터 수집 우선순위

**Status:** Record / notes only · **Normative authority:** None · **Owner:** Thomas
**작성일:** 2026-07-30 · **범위:** 통계·구조 문제 제외, **기대 수익 자체를 올리는 방향**만

> **구현 상태 (2026-07-30):** 아래 권장 순서의 **1~6번이 반영됐다** —
> taker order flow, 세션 라벨, mark/index/premiumIndex klines, BTC 상대강도, positioning store.
> 단 5번은 **수집만** 시작했고 **피처는 연결하지 않았다** — 30일 retention 대 500일 리플레이라
> 지금 연결하면 리플레이 창의 94%가 indeterminate이 된다. 진행 상황은 일일 보드의 "포지셔닝
> N/500일" 줄에 나오고, 실제 연결은 별도 결정이다.
>
> **6번(regime 조건부 라우팅)도 반영됐다.** 다만 본문이 예상한 형태와 다르다 — 본문은
> "`regime_breakdown`이 이미 regime별 R을 계산하므로 라우팅이 읽으면 된다"고 썼는데, 실제로는
> `regime_breakdown`이 그 값을 **계산한 뒤 버리고** 있었고(개수만 남김) 풀 엔트리에는 증거가
> 아예 없었다. 그래서 증거를 보존하고 승격이 풀로 복사하게 하는 일이 먼저였다. 7번 이하(변동성
> 타깃 사이징)도 반영됐다 — 다만 본문의 처방과 다르다. 본문은 `risk_fraction`을 변동성의
> 함수로 만들라고 했고 "예산 캡이 먼저 걸리면 효과 없음"을 경고했는데, 측정해 보니 그 경고가
> 맞았고 **그래서 처방이 틀렸다**: 캡이 걸리는 구간에서 `risk_fraction`은 완전히 무효다. 게다가
> 캡이 notional을 고정하므로 실제 리스크가 변동성에 비례해 커진다(측정: 6.7배 스윙) — 타깃팅의
> 정반대. 승수는 `min()` **이후 최종 수량**에 걸어야 한다.
>
> **8번(횡단면 롱숏, T3-3)까지 반영됐다 — 다만 절반이다.** 본문이 지목한 세 블로커
> (`route_entries` 단일 심볼 전제, `symbol_scope`, (symbol, timeframe) 포지션 북) 중 **어느 것도
> 건드리지 않았다.** 대신 코호트 순위를 **피처로** 만들었다(`xs_rank_pct` 외 4개) — 선언된
> 유니버스 안에서 이 심볼이 몇 번째로 강한가. 그러면 "상위 k개 롱 / 하위 k개 숏"이 기존 선언형
> 스펙 언어로 표현되고, 기존 백테스트가 채점하고, 기존 robustness 게이트가 판정한다. 새 권위가
> 하나도 안 생긴다. **그래서 절반인 이유**: 본문은 "시장 베타가 구조적으로 상쇄된다"고 했는데,
> 그 상쇄는 롱과 숏을 **동시에 보유할 때만** 일어난다. 지금은 두 패밀리가 각자 발화할 때
> 확률적으로 그렇게 되는 것이고, 강제되지 않는다.
>
> **그 포트폴리오 층 권위가 이어서 반영됐다 — 다만 "일방향으로 가능한 부분"만이다.** 기존
> `MAX_CONCURRENT_POSITIONS`(20)와 `MAX_POSITIONS_PER_SYMBOL`(4)은 포지션이 **몇 개**인지만
> 묶는다. 몇 개가 **같은 방향**인지는 아무것도 묶지 않았으므로 동시 롱 20개가 허용돼 있었고,
> 그게 정확히 F1 위험이다. 이제 같은 portfolio lock 안에서 세 번째 cap이 북의 **모양**을 읽는다
> (`paper.directional_skew_admits`, 한도 = `MAX_POSITIONS_PER_SYMBOL`에서 유도).
>
> **하지 않은 것, 그리고 그 이유**: k롱/k숏을 맞추기 위해 **없던 반대 포지션을 만드는** 일은
> 하지 않았다. 그건 어떤 전략도 제안하지 않은 엔트리를 런타임이 창조하는 것이고, 돈에 대한 새
> 권위이므로 Thomas 승인 사안이다. 지금 들어간 것은 **거절만 할 수 있는** 게이트라서
> (regime 게이트·변동성 승수와 같은 일방향 자세) 자기 게이트가 필요 없다. 즉 이건 **노출 규율**
> 이고 **강제된 시장 중립이 아니다.** 그 차이는 여전히 열려 있다.
>
> **전역 최적 엔트리(모든 컨텍스트를 평가한 뒤 실행하는 두 번째 패스)는 측정 후 짓지 않기로
> 했다.** 현재 풀 형태(#364로 컨텍스트당 전략 1개, 20 컨텍스트, 1,200봉)에서 **어떤 cap도 한
> 번도 걸리지 않았고**, 제안이 2개 이상인 봉은 1,200개 중 3개, cap이 거절하면서 다른 제안이
> 경쟁한 봉은 0개다 — **중재할 경합이 없다.** 게다가 중재기가 고칠 결함(발화하지 않는 전략으로
> 순위가 매겨진 컨텍스트가 더 약한 전략으로 슬롯을 가져가는 것)은 컨텍스트당 전략이 여러 개여야
> 성립하는데 `MAX_ROUTABLE_PER_CONTEXT = 1`이 그걸 금지한다. 그리고 중재기는 **일방향이 아니다**
> — 거절이 아니라 재배분이라 이 브랜치의 다른 증분들이 쓴 "거래를 줄이기만 하는 게이트는 자기
> 게이트가 필요 없다" 논거를 상속할 수 없다.
>
> 대신 **질문을 증거로 바꾸는 것**을 넣었다: cap 거절이 이제 counterfactual 그림자를 남긴다
> (cap 자신의 reason code로). C11은 C4 가드 차단만 그림자로 남기고 있었으므로, 세 개의 포지션
> cap이 무엇을 비용으로 치렀는지는 지금까지 시뮬레이션으로만 답할 수 있었다. 중재기는 이 기록이
> **실제 경합과 실제 비용**을 보여줄 때 지을 일이다.
>
> 근거·설계 이유는 `docs/BUILD_HISTORY.md`의 해당 항목에 있다. **이 문서 본문은 리서치 시점
> 그대로 두었다** — 무엇을 근거로 그 순서를 골랐는지가 기록의 값이고, 결과로 덮어쓰면 그게
> 사라진다.

`TRADING_STRATEGY_REVIEW_RECORD.md`가 "지금 측정하는 숫자가 믿을 만한가"를 다뤘다면, 이 문서는
"측정 대상 자체를 무엇으로 바꿔야 하는가"를 다룬다. 두 문서는 독립적이며, 여기 제안은 통계 층
수정과 무관하게 병렬로 진행할 수 있다.

---

## 0. 진단 — 지금 왜 엣지가 얇은가

**현재 feature row는 33개 컬럼인데, 그중 약 25개가 하나의 정보원(가격/거래량 시계열)의 변환이다.**

| 정보원 | 컬럼 | 비고 |
|---|---|---|
| OHLCV 시계열 | ma20/50, ema20/50, atr(×3), rsi, adx, macd(×3), bb(×5), roc_4, price_distance_ma20, volume_zscore, market_regime, htf_*(×5) | **~25개** |
| 펀딩 | funding_rate, funding_zscore | 2개 (8h 이벤트) |
| 청산 | long/short/total_liquidation, spike_ratio | 4개 |
| 미결제약정 | open_interest, change_pct, zscore | 3개 (일봉 해상도) |
| mark/index | mark_price, index_price, basis_bps | **3개 — 전부 죽어 있음** (`features.py:353`) |

20개 템플릿 패밀리(`factory.py:345`)가 mint하는 것은 결국 **같은 가격 시계열의 재조합**이다.
`trend_pullback`과 `macd_momentum`과 `bollinger_breakout`은 서로 다른 전략처럼 보이지만 같은
정보를 다르게 자른 것이다. 이 공간은 세계에서 가장 붐비는 공간이고, 5bps taker + 3bps 슬리피지 +
(모델링조차 안 된) 펀딩을 이기기에는 신호 대비 비용이 나쁘다.

**따라서 "전략을 더 만드는 것"의 기대값은 낮고, "독립 정보원을 늘리는 것"의 기대값이 훨씬
높다.** 아래는 그 관점으로 정렬했다.

그리고 가장 중요한 사실 하나:

> **네 번째 독립 정보원이 이미 매 HTTP 응답에 들어와 있는데 파서가 버리고 있다.**

---

## Tier 0 — 이미 받아놓고 버리는 데이터 (추가 요청 0, 추가 벤더 0, 추가 grant 0)

### T0-1. kline 응답의 필드 7~10 — **최우선**

Binance USD-M kline 배열은 **12개 필드**다:

```
[0] open time      [1] open   [2] high   [3] low   [4] close   [5] volume
[6] close time     [7] quote asset volume
[8] number of trades
[9] taker buy base asset volume
[10] taker buy quote asset volume        [11] ignore
```

현재 파서는 **`row[0]`~`row[6]`만 읽고 7~11을 버린다** (`market_data.py:508`):

```python
# Kline row: [open_time_ms, open, high, low, close, volume, close_time_ms, ...]
if not isinstance(row, list) or len(row) < 7:
```

버려지는 것 중 `[9] taker buy base volume`이 결정적이다. 이것으로 **봉 단위 order flow
imbalance**를 만들 수 있다:

```
taker_buy_ratio      = row[9] / row[5]                    # 0.5 = 균형
taker_flow_imbalance = (2 × row[9] − row[5]) / row[5]     # −1 … +1, 부호 있는 형태
```

이건 "거래가 얼마나 있었나"(volume)가 아니라 **"공격적으로 산 쪽이 누구인가"**다. 가격 시계열
에서 유도할 수 없는, 진짜 새로운 정보다.

**왜 이 시스템에 특히 잘 맞는가.** Kim & Hansen(2026)이 Binance 무기한 6종을 분석해 보고한 결과가
정확히 이 지점을 가리킨다 — 시/분 경계의 **order imbalance가 4~12시간 수익률을 예측한다**.
이 시스템의 `max_holding_bars`는 12~48(1h 기준 12~48시간)이고 주 타임프레임은 1h/4h다.
**연구가 보고한 예측 지평과 이 시스템이 실제로 잡는 보유기간이 겹친다.** 우연히 맞는 조합이다.

파생 지표도 전부 공짜다:

| 새 feature | 계산 | 의미 |
|---|---|---|
| `taker_buy_ratio` | `[9]/[5]` | 공격적 매수 비중 |
| `taker_flow_imbalance` | `(2·[9]−[5])/[5]` | 부호 있는 주문 흐름 |
| `taker_flow_zscore` | 위의 rolling z (기존 `indicators.zscore` 재사용) | 이상 흐름 감지 |
| `cvd_roc_n` | `taker_flow_imbalance`의 rolling sum 변화 | 누적 델타(CVD) 근사 |
| `avg_trade_size` | `[5]/[8]` | 거래당 평균 크기 — 고래/봇 참여 대리지표 |
| `trade_count_zscore` | `[8]`의 rolling z | 참여자 수 이상 |
| `quote_volume` | `[7]` | **심볼 간 비교 가능한** 명목 회전율 (base volume은 불가) |

**비판적으로 짚어둘 것:** `taker_flow_imbalance`와 가격 수익률은 **동시대적으로** 강하게
상관된다(사는 사람이 많으면 가격이 오른다 — 당연하다). 연구 문헌도 "trade flow imbalance는
동시대 가격 변화를 설명하는 데 더 낫다"고 명시한다. 따라서 **예측력을 주장하려면 반드시
`t` 시점 imbalance vs `t+k` 수익률로 검증해야 하고**, 이 시스템의 평가기는 같은 행만 보므로
(lag 연산자 없음) `roc_4` 같은 기존 컬럼과 함께 쓰면 조용히 동시대 정보를 섞을 위험이 있다.
가장 안전한 형태는 **직전 봉의 imbalance**(`prev_taker_flow_imbalance`)를 쓰는 것이다.

**구현 비용.** `Candle` dataclass 필드 4개 추가(`market_data.py:131`), `_parse` 인덱스 추가
(`:508`, `len(row) < 7` → `< 11`), `MockMarketDataCollector`도 같은 필드 생성(`:197`),
`build_feature_rows`에 컬럼 추가, `NUMERIC_FEATURES`에 등록, 템플릿 2~3개 추가.
**HTTP 요청 증가 0, 새 벤더 0, 새 safety flag 0, 500일 히스토리 즉시 확보.**
이 저장소에서 이만큼 싼 정보 획득은 다시 없다.

### T0-2. 시간대 / 세션 — 계산 비용 0

`timestamp`가 이미 행에 있다(`features.py:311`). 파생만 하면 된다.

암호화폐는 24시간 시장이지만 **참여자 구성은 시간대별로 크게 다르다.** 최근 연구 두 편이 이걸
정량화했다 — Shynkevich(*Journal of Futures Markets*, 2026)와 Kim & Hansen(2026)이 시 경계 1분,
15/30/45분 경계, 5분 경계 순으로 변동성·거래량 버스트가 체계적으로 존재하며 이것이 알고리즘
트레이딩의 흔적임을 보고한다.

다만 **`hour_of_day`를 그대로 numeric feature로 넣으면 안 된다.** validator가 numeric에 `==`를
허용하므로(`factory.py:137`) `hour_of_day == 3` 같은 조건이 mint되는데, 이건 24개 중 하나를 고르는
순수한 데이터 스누핑이다. 자유도 계산에도 안 잡힌다.

**권장 형태 — categorical 세션 라벨**로 `market_regime`과 같은 취급:

```python
SESSION = {"ASIA": 0..7, "EUROPE": 8..15, "US": 16..23}   # UTC
"session": 위 라벨,  "is_funding_hour": 0/8/16시 경계 여부
```

`CATEGORICAL_FEATURES`에 넣으면 `==`/`!=`만 허용되고(`factory.py:132`) 자유도가 3으로 묶인다.
`is_funding_hour`는 별개로 유용하다 — 펀딩 정산 직전/직후에 포지션 조정이 몰리는 구조적 패턴이 있고,
그 시점은 **완전히 예측 가능**하다.

---

## Tier 1 — 무료 공개 엔드포인트, 풀 히스토리 (요청만 추가)

### T1-1. `markPriceKlines` / `indexPriceKlines` / `premiumIndexKlines`

현재 mark/index/basis는 하드코딩된 가짜다:

```python
"mark_price": close, "index_price": close, "mark_index_basis_bps": 0.0,   # features.py:353
```

그런데 이 세 이름은 `NUMERIC_FEATURES`에 등록돼 있어서(`factory.py:100`) 팩토리가
`mark_index_basis_bps > 0.5` 같은 **영원히 거짓인 조건**을 mint할 수 있다. 즉 지금은 해로운 쪽이다.

Binance는 이 셋을 **klines와 동일한 형태·동일한 페이징(limit 1000/page)으로 전체 히스토리** 제공한다:

- `GET /fapi/v1/markPriceKlines`
- `GET /fapi/v1/indexPriceKlines`
- `GET /fapi/v1/premiumIndexKlines`

기존 `BinanceFuturesCollector`의 페이저(`market_data.py:455`)를 그대로 재사용할 수 있다.

**`premiumIndexKlines`가 특히 중요한 이유.** 지금 `funding_rate`는 **8시간 이벤트**를 as-of
정렬한 것이라(`features.py:245`) 1h 프레임에서 하루 3번만 값이 바뀌는 계단 함수다. G1의 OI 문제와
같은 구조다. premium index는 **봉 단위 해상도**로 같은 정보(펀딩을 결정하는 프리미엄)를 준다.

즉 `funding_fade_long` / `funding_fade_short` 패밀리가 **비로소 1h에서 제대로 타이밍을 잡을 수
있게 된다.** 지금 그 두 패밀리는 하루 3번 갱신되는 z-score로 1시간 봉을 판단하고 있다.

### T1-2. BTC 상대강도 — 횡단면의 저비용 버전

**근거.** 횡단면 모멘텀은 암호화폐에서 반복 검증된 몇 안 되는 효과다. 30일 기준 상위/하위 코인이
이후 7일간 상대적 성과를 지속하고, 롱숏 포트폴리오가 유의한 주간 알파를 낸다는 결과가 있다
(다만 같은 문헌이 **시계열 모멘텀이 횡단면보다 낫다**고도 보고한다 — 연 31.96% vs 14.59%.
과대평가하지 말 것).

**이 시스템에 더 직접적인 이유는 따로 있다.** 지금 모든 전략이 사실상 같은 베타에 걸려 있다.
BTC가 3% 빠지면 열려 있는 모든 롱이 동시에 손실이다(직전 리뷰의 F1). **상대강도는 그 공통 베타를
빼는, 추가 데이터가 거의 필요 없는 유일한 방법이다.**

완전한 횡단면 롱숏은 "한 사이클이 여러 심볼을 동시에 본다"는 구조 변경이 필요하지만, **저비용
버전은 BTC 캔들 하나만 더 받으면 된다**:

```
btc_roc_n              # BTC 자체 모멘텀 (시장 팩터)
rel_strength_btc       # 이 심볼 roc_n − btc_roc_n  (베타 제거된 초과 모멘텀)
btc_market_regime      # 시장 국면 (categorical, 기존 분류기 재사용)
corr_proxy_btc         # 이 심볼과 BTC 수익률의 rolling 상관 — F1 노출 통제에도 직접 쓰임
```

HTF 파이프라인이 이미 "다른 시계열을 계산해서 현재 봉에 as-of 정렬"하는 코드를 갖고 있다
(`features._htf_columns`, `:160`). BTC 컬럼은 **같은 함수를 다른 입력으로 부르는 것**에 가깝다 —
look-ahead 규칙(close_time 키)도 그대로 상속된다.

부수 효과가 큰 항목이다. `corr_proxy_btc`가 생기면 리스크 층에서 "이미 열린 포지션과 상관 0.9인
심볼에는 추가 진입 금지" 같은 규칙이 비로소 **표현 가능**해진다.

---

## Tier 2 — 무료지만 30일 retention → 자체 축적 필요

Binance의 `/futures/data/*` 통계 엔드포인트는 무료·무인증이지만 **최근 30일만** 제공한다
(rate limit 500/5min/IP):

| 엔드포인트 | 내용 | 이 시스템에서의 가치 |
|---|---|---|
| `takerlongshortRatio` | taker 매수/매도 볼륨 비율 | **T0-1과 중복** — 500일치를 이미 공짜로 얻으므로 우선순위 낮음 |
| `topLongShortPositionRatio` | 마진 상위 20% 계정의 **포지션** 편향 | **진짜 새로운 정보** |
| `topLongShortAccountRatio` | 마진 상위 20% 계정의 **계정 수** 편향 | 위와 쌍 |
| `globalLongShortAccountRatio` | 전체 계정 롱숏 비율 (리테일 대리) | 위와의 **차이**가 신호 |
| `openInterestHist` | OI (30일) | Coinalyze가 이미 커버 |

핵심은 **`topLongShortPositionRatio` − `globalLongShortAccountRatio`**다. 전자는 큰 계정의
포지션, 후자는 계정 수 기준 전체 분포다. 둘의 **괴리**가 "정보 있는 자본 vs 다수 계정"의 분리를
준다 — 이건 OHLCV에서 절대 유도할 수 없는 정보다.

**30일 벽은 이 저장소에서 이미 해결된 문제다.** `oi_store.py`가 정확히 그 패턴이다 —
"벤더의 retention wall 너머로 매 사이클 축적"(`oi_store.py:11`). 같은 패턴을 `positioning_store.py`로
복제하면 된다. 새 아키텍처가 아니라 **기존 선례의 재사용**이다.

**다만 정직하게 짚을 것 — 이건 오늘 수익을 못 만든다.** 축적 시작일 이전 데이터는 영원히 못
얻으므로, 백테스트는 6~12개월 뒤에나 의미 있는 표본을 갖는다. 그럼에도 **지금 시작할 가치가
있는 이유는 수집이 사실상 공짜이고, 나중에는 소급 생성이 불가능하기 때문이다.** 오늘 안 켜면
1년 뒤에도 여전히 30일치만 갖고 있게 된다.

**우선순위 판단: T0/T1을 먼저 하되, T2의 수집기는 "판단은 나중에, 축적은 지금" 원칙으로 병렬로
켜둘 것.**

---

## Tier 3 — 데이터가 아니라 전략 구조

### T3-1. 변동성 타깃 사이징 (기대값 대비 가장 싼 구조 변경)

현재 모든 거래가 고정 1%다(`live_sizing.py:49`). 변동성 타깃팅은 다자산·트렌드 추종에서
**Sharpe를 올리고 최대낙폭을 크게 낮추는** 것으로 반복 검증된 몇 안 되는 기법이다 — 한 실증에서
동일가중 → 역변동성 가중 전환만으로 Sharpe 0.99 → 1.54, MDD −30.8% → −13.8%.

**이 시스템은 이미 필요한 입력을 다 갖고 있다.** `atr_pct_of_price`와 `atr_percentile`이
행에 있으므로(`features.py:224`), `risk_fraction`을 상수 대신 변동성의 함수로 바꾸면 된다:

```
risk_fraction = base_fraction × clamp(target_vol / current_atr_pct, 0.5, 1.5)
```

주의: `size_live_order`는 이미 `min(risk-based, budget cap)`이라 실제로는 budget에 묶이는 경우가
많다(`live_sizing.py:185`). 캡이 60 USDT 수준이면 이 개선은 라이브에서 **아무 효과가 없다** —
캡이 먼저 걸리기 때문이다. 즉 이 항목은 **paper와 라이브 확대 이후**에 효과가 나온다.

### T3-2. 전략의 regime 조건부 활성화

지금은 풀에 들어간 전략이 항상 켜져 있고, 컨텍스트당 하나가 `champion_score`로 선택된다
(`paper.py:222`). `market_regime`/`htf_market_regime`이 이미 있으므로, **전략 레코드에
"이 전략이 유효한 regime"을 붙이고 그 밖에서는 라우팅에서 제외**하는 것이 가능하다.

`backtest_evidence.regime_breakdown`이 이미 regime별 R 합계를 계산한다(`factory.py:750`).
즉 **판단 근거가 이미 증거에 들어 있는데 라우팅이 그걸 안 읽는다.**

### T3-3. 진짜 횡단면 롱숏 — 가장 큰 기대값, 가장 큰 변경

한 사이클이 N개 심볼을 동시에 보고 상위 k개 롱 / 하위 k개 숏. 시장 베타가 구조적으로 상쇄되므로
F1(상관) 문제가 **부작용으로 함께 해결된다.** 다만 `route_entries`가 단일 심볼 전제이고
(`paper.py:151`), `symbol_scope`가 사실상 단일 심볼이며(`factory.py:511`), 포지션 북이
(symbol, timeframe) 단위다. 앞의 항목들이 끝난 뒤 별도로 다룰 주제다.

---

## 명시적으로 **하지 말 것**

### ❌ 펀딩 캐리 / 베이시스 아비트라지
구조적으로 불가능하다. 델타 뉴트럴이려면 **spot 롱 + perp 숏**이 필요한데 이 시스템은 단일 leg
선물 전용이다(`live_leg.py`). 게다가 수익성도 나빠졌다 — 2026년 2월 주요 토큰 펀딩이 역사적
하위 3~15% 구간이었다는 보고가 있다. 흔히 인용되는 "연 10~30%"는 spot leg를 갖춘 델타 뉴트럴
기준이고, **레버리지·거래소 리스크·펀딩 부호 반전 리스크를 뺀 숫자**다.

### ❌ 호가창 / 틱 데이터
`/fapi/v1/depth`, `aggTrades`는 무료지만 **폴링 빈도가 완전히 다른 아키텍처**를 요구한다.
현재 15분 사이클과 맞지 않고, 스냅샷 기반 결정론적 리플레이 모델과도 충돌한다. `taker_buy_volume`
(T0-1)이 봉 단위로 **주문 흐름 정보의 상당 부분을 이미 준다** — 비용 대비 훨씬 낫다.

### ❌ TA 지표를 더 추가하는 것
가장 흔한 함정이다. 이미 ~25개 컬럼이 하나의 시계열에서 나온다. 스토캐스틱, Ichimoku, VWAP,
Supertrend를 더해도 **새 정보는 0이고 자유도만 늘어난다** — 그리고 자유도는 robustness 점수의
분모다(`robustness.py:84`). 순수한 마이너스다.

### ❌ 심볼 개수를 늘리는 것 (지금은)
현재 5심볼 × 4타임프레임 = 20 컨텍스트인데 상관 통제가 없다. 심볼을 늘리면 후보 수와 다중검정
문제만 커지고 실질 분산은 거의 안 는다. **`corr_proxy_btc`(T1-2)로 상관을 측정할 수 있게 된
다음에** 늘리는 것이 순서다.

---

## 권장 실행 순서

| 순서 | 항목 | 요청 증가 | 새 벤더 | 히스토리 | 기대 효과 |
|---|---|---|---|---|---|
| 1 | **T0-1 taker flow (kline 7~10)** | **0** | **0** | **즉시 500일** | **최대** |
| 2 | T0-2 세션 라벨 (categorical) | 0 | 0 | 즉시 | 중 |
| 3 | T1-1 premiumIndex / mark / index klines | 심볼당 +1~3 | 0 | 전체 | 중~상 |
| 4 | T1-2 BTC 상대강도 + 상관 | 사이클당 +1 | 0 | 전체 | 상 (리스크 층에도) |
| 5 | T2 positioning store (축적 시작) | 심볼당 +3 | 0 | **오늘부터** | 장기 |
| 6 | T3-2 regime 조건부 라우팅 | 0 | 0 | — | 중 |
| 7 | T3-1 변동성 타깃 사이징 | 0 | 0 | — | 캡 완화 후 |
| 8 | T3-3 횡단면 롱숏 | — | 0 | — | 최대, 최대 비용 |

**1번 하나만으로도 이 저장소에서 가장 비용 대비 효과가 큰 변경이다.** 이미 다운로드하고 있는
바이트를 파싱하는 것뿐이고, 얻는 것은 **가격 시계열에서 유도 불가능한 네 번째 독립 정보원**이며,
그 정보원의 예측 지평(4~12시간)이 이 시스템의 보유기간(12~48시간)과 겹친다.

---

## 검증 없이 믿지 말 것

위 근거는 **문헌과 엔드포인트 사양**이지, 이 시스템·이 심볼·이 비용 구조에서의 실증이 아니다.
특히:

- taker flow의 예측력은 **동시대 상관과 분리해서** 측정해야 한다 (T0-1의 경고 참조)
- 인용한 Sharpe·수익률 수치는 전부 **다른 비용 구조·다른 유니버스·다른 기간**의 것이다
- 새 feature는 자유도를 늘리므로, 다중검정 보정 없이 추가하면 **직전 리뷰의 A1 문제를 키운다**.
  구조 브랜치의 통계 작업과 순서를 맞추는 편이 안전하다

각 feature는 추가 후 **단독으로** — 기존 패밀리와 섞지 말고 — 자기 템플릿 패밀리 하나로 먼저
채점해서 그 정보원 자체에 신호가 있는지 확인하는 것이 맞다.

---

## 출처

- [The Quarter-Hour Effect: Periodic Algorithmic Trading and Return Predictability in Cryptocurrency Futures (Kim & Hansen, arXiv 2607.09426)](https://arxiv.org/abs/2607.09426)
- [Trading Periodicity and Algorithmic Divide in Cryptocurrency Markets (Shynkevich, Journal of Futures Markets, 2026)](https://onlinelibrary.wiley.com/doi/abs/10.1002/fut.70089)
- [Explainable Patterns in Cryptocurrency Microstructure (arXiv 2602.00776)](https://arxiv.org/html/2602.00776v1)
- [Order Flow and Cryptocurrency Returns (EFMA 2025)](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2025-Greece/papers/OrderFlowpaper.pdf)
- [Binance — Kline/Candlestick Data (USD-M)](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [Binance — Premium Index Kline Data](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Premium-Index-Kline-Data)
- [Binance — Mark Price Kline Candlestick Data](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price-Kline-Candlestick-Data)
- [Binance — Index Price Kline Candlestick Data](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Index-Price-Kline-Candlestick-Data)
- [Binance — Long/Short Ratio (futures/data)](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Long-Short-Ratio)
- [Binance — Top Trader Long/Short Position Ratio](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Top-Trader-Long-Short-Ratio)
- [Binance — Open Interest Statistics](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Open-Interest-Statistics)
- [Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market (AUT ACFR)](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)
- [A Trend Factor for the Cross Section of Cryptocurrency Returns (JFQA, Cambridge)](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/4C1509ACBA33D5DCAF0AC24379148178/S0022109024000747a.pdf/trend_factor_for_the_cross_section_of_cryptocurrency_returns.pdf)
- [Cross-sectional Momentum in Cryptocurrency Markets (Starkiller Capital)](https://www.starkiller.capital/post/cross-sectional-momentum-in-cryptocurrency-markets)
- [The Impact of Volatility Targeting (Man Group)](https://www.man.com/insights/the-impact-of-volatility-targeting)
- [An Introduction to Volatility Targeting (QuantPedia)](https://quantpedia.com/an-introduction-to-volatility-targeting/)
- [Periodicity in Cryptocurrencies – Recurrent Patterns in Volatility and Volume (QuantPedia)](https://quantpedia.com/periodicity-in-cryptocurrencies-recurrent-patterns-in-volatility-and-volume/)
