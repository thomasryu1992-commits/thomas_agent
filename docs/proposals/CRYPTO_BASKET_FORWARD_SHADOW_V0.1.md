# 제안: 바스켓 forward 섀도 — 주문도 paper 포지션도 없이, 매주 바스켓을 결정해 적고 다음 주에 매긴다 (DRAFT v0.1)

**상태:** DRAFT 2026-11-04 — Thomas: 바스켓 forward 측정을 거버넌스 제안으로 설계한다("거버넌스 제안으로 설계하자"). 형태·문 효과·데이터·factory는 기본값으로 정했다("기본값으로 하는데"). 판정 기간은 최대한 줄이고, 판정은 현물 홀드아웃(`CROSS_SECTION_SPOT_HOLDOUT_V0.1`)이 맡는다. 이 섀도는 감시다. 새 kind·기록·store를 여는 것이라 §7의 승인 뒤에 짓는다.
**대상:** `crypto/` 레인, 스케줄러 유지보수 kind 하나.
**기준 시점:** origin/main `7b9310a2`.
**표기:** [확인] 코드로 확인, [문서] 저장소 기록, [추정] 판단.

## 0. 한 줄

v0.2에서 LEAD였던 S-MOM30 바스켓(상위 30, 한쪽 6, 주간)을 **실제 시간 속에서** 따라간다. 매주 일요일 종가 기준 바스켓을 결정해 적고, 다음 주 월요일 시가부터 그 주의 수익·비용·펀딩을 매긴다. 주문, paper 포지션, 풀, forward book에는 닿지 않는다. 자기 store만 쓴다.

## 1. 왜 포트폴리오 층이 아니라 섀도인가 [확인]

- **paper 장부에 다리를 여는 방식은 새 권한이다.** "아무 전략도 제안하지 않은 진입을 런타임이 만든다"는 것이다 [문서, `BUILD_HISTORY.md` 2026-07-30; `CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md` §11].
  - paper 장부의 용량 한도와 겹친다. 동시 20개 한도 중 12개를 차지한다(`paper.MAX_CONCURRENT_POSITIONS`).
  - 기존 증거 장부에도 섞인다.
- **섀도는 측정이다.** `crypto_forward_cohort`와 같은 모양이다(`scheduler.py` `KIND_FORWARD_COHORT`). "no pool, no orders"이고 자기 store만 쓰는 유지보수 kind다.
- 그래서 진입 권한은 늘지 않는다. 늘어나는 것은 kind 하나, 기록 하나, store 하나다.

## 2. 무엇을 하나

**fire (하루 한 번, 스케줄은 간격만 지원하므로 창 판단은 fire 안에서 한다):**
- **결정.** 직전 일요일 종가 기준 결정이 없으면 하나를 적는다.
  - 코인 무기한 목록: exchangeInfo `contractType == PERPETUAL`.
  - 각 심볼의 일봉 31개로 30일 quote volume 상위 30을 고른다(상장 90일 규칙 포함).
  - 30일 수익률 상위 6개를 롱, 하위 6개를 숏한다. 무게는 ±1/6이다.
- **매김.** 결정이 있고 그 주가 끝났으면(다음 월요일 시가 봉이 있으면) 그 주를 매긴다.
  - 다리별 시가→시가 수익.
  - 회전 비용: taker 5 + 슬리피지 3 bps, `crypto/cost.py`.
  - 그 주에 정산된 펀딩: 롱이 내고 숏이 받는다.
  - 상장폐지된 다리는 마지막 종가에서 닫는다(v0.2와 같은 규칙).
- **놓친 주는 따라잡는다.** fire가 며칠 늦으면, 빠진 결정과 매김을 순서대로 채운다. 단, 결정은 그 일요일 종가까지의 봉으로만 만든다.

**규칙의 출처:** `scripts/research/cross_section_basket.py`의 v0.2 정의를 그대로 쓴다. 런타임과 연구 코드가 같은 규칙을 두 번 구현하지 않도록, 순위·무게·비용 계산은 한 모듈에 둔다. 위치는 구현 PR에서 정한다(§6).

## 3. 기록과 store

- **store:** `crypto/basket_shadow.jsonl`. 추가만 하고, 각 줄은 `record_sha256`로 봉인한다.
- **기록:** `basket_shadow.v0.1`, 닫힌 스키마(`additionalProperties: false`)를 둔다.
  - `decision`: 주(월요일 날짜), 결정 시각, 유니버스 해시, 롱·숏 심볼과 무게, 입력 봉 다이제스트, 규칙 버전(v0.2).
  - `mark`: 주, 다리별 수익, 총수익, 비용, 펀딩, 순수익, 강제 청산 수.
- **비밀값은 없다.** 공개 시장 데이터만 쓴다.

## 4. 데이터와 게이트

- **새 환경변수와 새 공급자는 없다.** 이미 열린 `MVP_MARKET_DATA=binance_futures` 게이트의 수집기(`market_data.select_market_data_collector`)로 읽는다.
  - klines, fundingRate, exchangeInfo는 런타임이 이미 쓰는 엔드포인트다 [확인, `market_data.py`].
  - 다만 호출 지점이 하나 늘어난다. `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named`의 명단에 이름을 올린다.
- **요청량:** 주 1회 결정에 코인 무기한 약 640개 × 일봉 1회, 그리고 들고 있는 12개의 펀딩. klines 가중치 기준으로 분당 한도(2,400)의 절반을 넘지 않게 나눠 보낸다 [추정: 공개 한도 기준].
- **mock일 때:** 게이트가 닫혀 있으면 mock 수집기가 선택된다. 이때 섀도는 아무것도 적지 않고 "inert"로 상태 줄만 남긴다. mock 데이터로 forward 기록을 만들지 않는다.

## 5. 감시 규칙 (판정이 아니다)

- **이 섀도는 확인 판정을 내리지 않는다.** 확인은 현물 홀드아웃이 한다.
- **멈춤 표지:** 26주가 지나면 매주 누적 주 평균의 단측 p(평균 < 0)를 계산한다. p ≤ 0.10이면 `STOP_SIGNAL`을 적고 Thomas에게 알린다.
  - 표지는 아무것도 자동으로 끄지 않는다. 다음 단계(paper 층 등)를 멈출지는 Thomas가 정한다.
- **문 효과는 없다.** 이 기록은 계보 단위의 FORWARD_CONFIRMED가 아니다. LIVE 문, D5, 실행 단계, 풀, 판정 규칙에 영향이 없다.

## 6. 짓는 것 (승인 뒤, Claude PR)

- `crypto/basket_shadow.py`: 결정, 매김, 따라잡기, 상태 줄.
- `scheduler.py`: `KIND_BASKET_SHADOW = "crypto_basket_shadow"`.
  - `KINDS`와 `MAINTENANCE_KINDS`에 넣는다.
  - `_execute`에서 함수 안 import로 부른다. 도메인 격리 규칙에 따라 `EXPECTED_DOORS`는 비워 둔다.
- `store.py`: 기록 종류를 등록한다. `schemas/basket_shadow.v0.1.schema.json`을 둔다.
- 순위·무게·비용 계산을 연구 스크립트와 공유하는 모듈 하나. 공유할 수 없으면 두 구현이 같은 결과를 내는지 고정하는 시험을 둔다.
- **시험:**
  - 결정이 일요일 종가 뒤의 값을 읽지 않는다.
  - 따라잡기가 빠진 주를 순서대로 채운다.
  - mock이면 아무것도 적지 않는다.
  - 닫힌 스키마를 지킨다.
  - 주문·paper·풀 store를 건드리지 않는다.
  - 게이트 명단 테스트를 갱신한다.
- **배포 뒤:** 스케줄 줄을 하나 더한다(`scheduler_cli add --kind crypto_basket_shadow --interval-seconds 86400`). factory 창(08:00–09:00 UTC)은 피한다.

## 7. Thomas 결정 요청

- **Q1.** 새 유지보수 kind `crypto_basket_shadow`, 기록 `basket_shadow.v0.1`, store `crypto/basket_shadow.jsonl`을 연다. 기본값: 연다.
- **Q2.** 감시 규칙(§5): 26주 뒤부터 매주 단측 p ≤ 0.10이면 멈춤 표지를 적고 알린다. 자동으로 끄는 것은 없다. 기본값: 그대로.
- **Q3.** 알림 경로: 멈춤 표지와 매주 한 줄 요약을 기존 알림 창(send-only)으로 보낼지. 기본값: 멈춤 표지만 보낸다. 주간 요약은 문(door) 조회로만 본다.
- **Q4.** 국면별 전략(`CRYPTO_REGIME_STRATEGY_SET_V0.1`)이 확인되면 같은 섀도에 넣을지. 기본값: 그때 따로 묻는다.

## 8. 한계 [추정]

- 섀도는 일봉 시가 체결을 가정한다. 실제 주문의 슬리피지, 부분 체결, 숏 가능 여부는 보지 않는다. 그건 paper 층이나 실거래의 몫이다.
- 매주 640개 심볼을 읽는 동안 한 심볼이라도 실패하면 그 주 유니버스가 달라질 수 있다. 실패한 심볼 수를 결정 기록에 적고, 10%가 넘으면 그 주 결정을 BLOCK한다(추측으로 채우지 않는다).
