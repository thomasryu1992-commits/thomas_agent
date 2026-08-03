# 제안: S1 — Hyperliquid 읽기 전용 수집기 (DRAFT v0.1)

**상태:** DRAFT — 설계 선행(구현 아님).
**부모:** [EQUITY_PERP_LANE_V0.1.md](EQUITY_PERP_LANE_V0.1.md) — 레인 전체 설계와 S0–S5 시퀀싱 (#441).
**범위:** 캔들 수집기 하나. **주문 없음, 계좌 없음, 페이퍼 없음.**
**선행:** S0(규제 판단 기록)은 여전히 미비준이다. 이 문서는 S0를 대체하지 않으며, S1 착수
가부는 S0 이후다.

---

## 0. 왜 S1이 게이트가 닫힌 지금 오히려 급한가

부모 §2가 측정한 대로 팩토리 깊이 게이트는 **닫혀 있다**(49–138일 / 500일). 그러면 기다리면
될 것 같지만, **기다리는 동안 데이터가 사라진다.**

`candleSnapshot`은 **최근 5000개 롤링 윈도우**다. 1h에서 5000시간 = **208일**이므로:

| 심볼 | 현재 히스토리 | 1h 롤오프 시작까지 |
|---|---|---|
| `xyz:SP500` | 138일 | **약 70일 후** |
| `xyz:XLE` | 91일 | 약 117일 후 |
| `xyz:SMH` | 49일 | 약 159일 후 |

**롤오프가 시작되면 그 이전 1h 봉은 베뉴에서 영구히 사라진다.** 팩토리가 요구하는 500일치
1h 데이터는 자체 적재 없이는 **영원히 도달 불가**다. 4h(5000봉 = 833일)와 1d는 여유가 있으므로,
위험한 것은 **1h 이하 전부**다.

그러므로 S1의 실질 가치는 어댑터 코드가 아니라 **시계를 지금 시작시키는 것**이다. 이것이
"게이트가 닫혔으니 나중에"가 틀린 결론인 이유다.

## 1. 어댑터 계약 — 발명이 아니라 기존 형태의 복제

`BinanceFuturesCollector`에서 읽어낸 형태를 그대로 따른다.

```
class HyperliquidCollector:
    tool_id = MARKET_DATA_TOOL_ID          # 공유
    tool_version = "0.1.0-hyperliquid"     # Binance는 "0.1.0-binance"
    provider_id = HYPERLIQUID              # 세이프티 게이트 그랜트 이름
    network_egress = True
    source = "hyperliquid_public"

    def __init__(self, *, authorization: Authorization | None = None): ...
    def collect(self, symbol, timeframe, *, limit, timeout_seconds) -> MarketSnapshot: ...
    def funding_history(self, symbol, *, records, timeout_seconds) -> list[dict]: ...
```

**게이트 통과 지점도 동일**하다 — `safety_gate.select_gated(...)`로만 구성되고, `collect`는
egress 순간에 authorization을 **재검증**한다(R3 어댑터 자세, Binance 수집기가 하는 것과 같음).

**주의: `MarketDataCollector` Protocol은 `collect` 하나만 선언하지만 실제 호출 표면은 더 넓다.**
`cycle.py`가 `collector.funding_history`와 `collector.derivative_price_klines`를 직접 부르고,
청산·미결제약정은 `select_liquidation_feed`라는 **별도 셀렉터**를 탄다. Protocol만 만족시킨
어댑터는 런타임에서 깨진다 — §4가 이 격차를 다룬다.

## 2. 요청/응답 매핑 — 측정값 (2026-08-03)

**요청:** `POST https://api.hyperliquid.xyz/info`

```json
{"type":"candleSnapshot","req":{"coin":"xyz:XLE","interval":"1h","startTime":0,"endTime":<ms>}}
```

**응답 원소(실측):**

```json
{"t":1777852800000,"T":1777939199999,"s":"xyz:XLE","i":"1d",
 "o":"58.939","c":"59.345","h":"60.7","l":"58.2","v":"5221.48","n":365}
```

**`Candle`로의 매핑:**

| Candle 필드 | 출처 | 비고 |
|---|---|---|
| `open_time` | `t` | ms |
| `close_time` | `T` | ms |
| `open`/`high`/`low`/`close` | `o`/`h`/`l`/`c` | **문자열 → float 파싱 필요** |
| `volume` | `v` | 문자열 → float |
| `trade_count` | `n` | 정수 |
| `quote_volume` | **없음** | → `None` |
| `taker_buy_base` | **없음** | → `None` |
| `taker_buy_quote` | **없음** | → `None` |

마지막 셋은 **구조적 부재**이며 수집 실패가 아니다. `collect_market_data`가 이미 올바른 자세를
문서화하고 있다 — 키를 **남기고 None**을 넣는다. 주석 그대로: *"스냅샷은 베뉴가 보고한 것을
진술한다. 아무것도 보고하지 않았다는 사실을 포함해서."* 키를 지우면 "베뉴가 플로우 전송을
멈췄다"와 "이 스냅샷은 플로우 레그 이전 것이다"를 구분할 수 없게 된다.

**타임프레임 문자열은 그대로 통한다.** `strategy.ALLOWED_TIMEFRAMES = {15m, 1h, 4h, 1d}`이고
베뉴도 동일 문자열을 쓴다. 매핑 테이블 불필요.

## 3. 측정으로 드러난 구체적 걸림돌 4개

### 3.1 심볼 검증이 모든 HIP-3 이름을 거부한다 ⚠️

`_SYMBOL_PATTERN = re.compile(r"\A[A-Z0-9]{5,20}\Z")`, `_require_symbol`이 `collect_market_data`
첫 줄에서 부른다. 실측:

| 심볼 | 통과 |
|---|---|
| `BTCUSDT` | ✅ |
| `xyz:XLE` | ❌ 소문자 + 콜론 |
| `XLE` | ❌ 5자 미만 |
| `xyz:SP500` | ❌ |

**HIP-3 이름은 `dex:ticker`가 베뉴의 정식 형식**이므로 `xyz:`를 떼는 것은 잘못된 해법이다 —
`AVGO`는 `xyz`에도 `para`에도 있고, 접두어가 그 둘을 가르는 유일한 것이다.

→ 심볼 검증을 **베뉴별**로 만든다. 베뉴가 자기 패턴을 선언하고, `_require_symbol`이 그것을
읽는다. 기본값은 현행 패턴 그대로여서 크립토 경로는 바뀌지 않는다.

### 3.2 셀렉터가 단일 opt-in 값 모양이다

`select_gated(env_var=MARKET_DATA_ENV, opt_in_value=BINANCE_FUTURES, ...)` — 값 하나와 매치하는
구조다. 두 번째 베뉴는 **값에 따라 분기**해야 한다. LLM 프로바이더 체인(`select_gated_chain`)과는
다른 문제임에 유의: 저쪽은 **failover**이고 이쪽은 **택일**이다. 시장 데이터는 주석이 명시하듯
*"백엔드 하나 — 수집은 failover가 아니라 degrade한다"*. **체인을 재사용하면 안 된다.**

### 3.3 형성 중인 캔들이 응답에 포함된다 (실측)

`endTime = now`로 1h를 요청하면 마지막 원소의 `T`가 미래다 — 아직 닫히지 않은 봉이다.
`BinanceFuturesCollector`는 이것을 떨어뜨린다(*"닫힘값이 아직 움직이는 캔들을 하위 지표가 절대
보지 않도록"*). **동일하게 떨어뜨려야 하며, 이는 어댑터의 책임이지 호출자의 책임이 아니다.**

### 3.4 페이징이 필요 없고, 필요 없다는 사실이 기록되어야 한다

Binance 수집기는 `PAGE_LIMIT=1500`, `MAX_PAGES=60`으로 뒤로 판다. Hyperliquid는 한 번에 5000개를
주고 **그 너머에 아무것도 없다.** 그러므로 페이징 루프는 불필요하다 — 그러나 `limit > 5000`
요청이 들어오면 **조용히 5000개를 반환하면 안 된다.** 호출자(팩토리)는 `factory_candle_target`으로
1h에서 12,000봉을 요구하는데, 받은 것이 5000개이고 그게 전부라는 사실이 기록되지 않으면
"얕은 창"이 "정상 수집"으로 보인다. → 스냅샷에 **깊이 상한에 걸렸다는 표시**가 필요하다.

## 4. 없는 피드의 처리 — 4층

진입 시점은 이미 안전하다. `strategy.py`가 명시한다: *"missing/None 피처에 대한 조건은
indeterminate이며 조용한 매치가 아니다."* **그것이 문제다** — 없는 피처를 참조하는 전략은
조용히 영원히 발화하지 않고, 표본이 쌓이지 않아 라이프사이클 사다리가 강등할 수 없으며,
라우팅 슬롯을 영구 점유한다. BUILD_HISTORY가 손실 브레이커에서 이미 기록한 latch 병리다.

1. **구조적 부재 ≠ 일시적 부재.** `degraded`는 *"있어야 하는데 이번엔 없었다"*이며 풀릴 수 있다는
   뜻이다. 주식 베뉴의 미결제약정은 **영원히 안 풀린다.** 재사용하면 매 사이클 열화 알림이
   울리고 운영자가 그 채널을 무시하도록 학습한다 — 진짜 열화를 놓치는 경로다. `unsupported`를
   **별개 상태**로 둔다.
2. **역량은 선언한다, 탐지하지 않는다.** 베뉴가 보유 피드를 선언하고 피처 어휘가 거기서 파생된다.
   **선언되지 않은 피드는 "없음"이 아니라 "모름"이고, 모름은 BLOCK이다**(CLAUDE.md fail-closed).
3. **게이트는 진입이 아니라 검증 시점** (Thomas 결정, §5). `strategy.referenced_features()`가 이미
   있다. *참조 피처가 스펙 자신의 베뉴에서 전부 가용하지 않으면 그 스펙은 무효다.* **새 게이트를
   만들지 않는다** — `proposer.unknown_features()`는 스스로 *"Diagnostic, not a gate"*라 적었고
   실제 게이트는 `factory.validate_strategy`의 `BLOCK_UNKNOWN_FEATURE`다. 베뉴 검사는 그 검증기에
   합류하며, 베뉴는 스펙이 들고 온다(§5).
4. **중립값 날조 금지.** `_reference_columns`가 선례다 — 심볼이 레퍼런스 자신일 때 0.0/1.0 대신
   None을 반환한다(*"주조된 조건이 걸릴 수 있는 날조 상수"*). 없는 OI가 `oi_zscore = 0.0`이
   되면 안 된다. 그리고 `vol_size_multiplier` 선례대로 **부재를 명시적으로 기록**한다.

**주식 베뉴의 실제 피드 상태:**

| 피드 | 상태 |
|---|---|
| 캔들 (`candleSnapshot`) | ✅ |
| 펀딩 (`fundingHistory`) | ✅ |
| 미결제약정·청산 | ❌ 구조적 부재 (Coinalyze는 크립토 전용 벤더) |
| 포지셔닝 비율 | ❌ 구조적 부재 (바이낸스 고유) |

→ `oi_store`·`positioning_store`가 먹이는 피처군과 `_oi_squeeze_*` 계열 전략 템플릿이 이 베뉴에서
**꺼진다.** §4.3의 검증기 합류가 이것을 라우팅 이전에 잘라내는 지점이다.

## 5. 확정된 결정 — 베뉴 검사는 검증기가 한다 (Thomas, 2026-08-03)

`factory.validate_strategy`가 베뉴를 안다. 대안이었던 "풀의 routable 판정 쪽"은 무효한 스펙이
주조는 되고 라우팅만 안 되는 중간 상태를 만든다. 스펙 유효성은 원래 그 모듈의 질문이고, 베뉴는
그 질문의 **컨텍스트**지 별개 질문이 아니다.

### 그래서 베뉴가 검증기에 어떻게 도달하는가 — 인자가 아니라 스펙에 실어서

두 가지 방식이 있고, 갈림길이 보이는 것보다 중요하다.

**(가) 호출 인자 `validate_strategy(spec, *, venue)`.** 스키마 변경이 없다. 호출부는 셋
(`factory.py:1169` 주조, `factory.py:1852` 자식 생성, `proposer.py:335` LLM 제안).
**그러나 이 방식은 정확히 한 가지를 허용한다: Hyperliquid 데이터로 주조된 스펙을 Binance 어휘로
검증해서 통과시키는 것.** 스펙과 그것이 채굴된 베뉴 사이에 아무 구속이 없기 때문이고, 나중에
풀이 "이 스펙은 베뉴 V에서 routable인가"를 물을 때도 V를 바깥에서 다시 공급해야 한다. 같은 사실이
두 곳에서 따로 주장되면 언젠가 갈라진다.

**(나) `StrategySpec`이 `venue`를 갖는다. 검증기는 `spec.venue`를 읽는다.** ← **채택.**
스펙은 **특정 베뉴의 데이터로 채굴된 물건**이고, 그 출처는 스펙과 분리 가능해서는 안 된다.
호출부 세 곳은 바뀌지 않으며(인자가 늘지 않는다), 베뉴는 기록과 함께 영구히 이동한다.
`venue`는 이미 `paper` 포지션과 예산 스키마에서 일급 필드다 — 새 개념이 아니라 이미 있는
개념을 스펙까지 넓히는 것이다.

**대가: 스펙 스키마 버전 범프.** `validate_strategy`가 이미 `spec.schema_version != SCHEMA_VERSION`을
검사하므로 버전 메커니즘은 존재한다. **저장된 기존 스펙에 `venue`가 없으면 `binance_futures`로
읽는다** — 이건 검증기 기본값과 성질이 다르다. 그 필드가 존재하기 전에 쓰인 레코드는 **다른 베뉴가
없었으므로 Binance임이 증명된다.** 반면 베뉴를 빠뜨린 호출자는 아무것도 증명하지 않는다.
읽기 측 마이그레이션 기본값은 허용하고, 검증기 기본값은 두지 않는 이유다.

**어휘 조회:** `NUMERIC_FEATURES` / `CATEGORICAL_FEATURES`가 베뉴 스코프가 된다
(`known_features(venue)`). **미지의 베뉴는 BLOCK이다** — 빈 어휘로 조용히 통과시키지 않는다
(CLAUDE.md fail-closed). 새 `reason_code`는 필요 없다: 참조 피처가 그 베뉴에 없으면
기존 `BLOCK_UNKNOWN_FEATURE`가 정확히 맞는 진술이다.

## 5b. 남은 열린 결정 (1건)

**깊이 상한 표시를 어디에 둘 것인가(§3.4).** 스냅샷 필드인가, `degraded`와 나란한 별도 상태인가.
`is_synthetic`이 이미 "이 데이터가 무엇인지"를 말하는 자리이므로 그 옆이 자연스럽다.

## 6. S1이 인도하는 것

- `HyperliquidCollector` (Mock 우선 → 게이트 통과 네트워크), 베뉴별 심볼 검증, 형성 캔들 제거,
  깊이 상한 표시
- **`StrategySpec.venue` 필드 + 스키마 버전 범프**, 베뉴 스코프 피처 어휘(`known_features(venue)`),
  그리고 `validate_strategy`가 그것을 읽는 것(§5). 저장된 기존 스펙은 `binance_futures`로 읽는다.
  **크립토 경로의 판정 결과는 이 변경 전후로 동일해야 한다** — 그것을 고정하는 테스트가 이 항목의
  수용 기준이다
- **게이트가 열리는 날짜** — 코호트 전 구성원의 상장일에서 도출. 코드가 아니라 달력이 v1 일정을
  지배한다는 사실의 구체적 산출물
- **코호트 가용률** — `MIN_CROSS_SECTION_MEMBERS = 4` 위험의 실측
- **수수료 실측** — 부모 §5에서 유일하게 공개 자료로 남은 항목. S2 하드 게이트의 입력

## 7. S1이 하지 않는 것

주문 어댑터, 계좌 피드, 페이퍼 레그, 나머지 상수의 베뉴별 전환(S3), 비용 모델(S2).
`REFERENCE_SYMBOL`/`CROSS_SECTION_UNIVERSE`는 S3이며, S1이 옮기는 것은 피처 **어휘**뿐이다 —
어휘는 "그 베뉴에 무엇이 존재하는가"이고 코호트는 "무엇을 거래하기로 했는가"라서 서로 다른 질문이다. 그리고 **라이브
경로는 어느 것도 건드리지 않는다** — S1이 끝나도 크립토 레인의 동작은 바이트 단위로 동일해야
하고, 그것이 이 증분의 수용 기준이다.
