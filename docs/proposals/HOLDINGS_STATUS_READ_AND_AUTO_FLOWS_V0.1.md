# 제안: Claude가 holdings 검사 상태를 읽고, Toss 입출금은 규칙으로 자동 분류한다 (DRAFT v0.1)

**상태:** DECIDED 2026-10-26 — R1 예, R2 예, R3 예, F1 A, F2 아니오(Thomas). PR-1(R1–R3: `toss_needs`, `holdings_status` v1.1, Claude 프로필, CLAUDE.md)을 짓는 중이고, PR-2(F1–F2: `quiet_window.v1`)가 남았다. **결정 전 상태:** DRAFT — Thomas 결정 다섯 건(R1–R3, F1–F2)을 기다렸다. Thomas가 두 가지를 요청했다(2026-10-26). 하나는 holdings 검사의 이름과 상태만 Claude read door로 읽는 것이다(금액 없음). 다른 하나는 입출금을 사람이 닫지 않고 자동으로 확인하는 것이다. 코드·정책·스키마 변경은 없다.

**대상:** Claude read door의 `holdings_status` 읽기(`HOST_PRIVILEGE_SEPARATION_V0.1.md` §4.19.2의 응답 계약 v1)와 H6 현금흐름 원장의 Toss 잔차 처리(`PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`, `holdings/cash_flows.py`).

**기준 시점:** 2026-10-26, origin/main `4b6953fe`. [확인]은 코드·공식 문서로 확인한 것이다. [추정]은 계산하거나 짐작한 것이다.

---

## 0. 한 줄

- **Claude가 holdings 보드를 볼 수 없다.** C6b 이후 Claude는 docker를 쓰지 못한다. read door는 `schedules`·`scheduler_events`만 읽는다. 그래서 H6 준비 상태를 확인할 때마다 Thomas가 보드를 읽어 옮겨야 한다.
  - 기존 `holdings_status` 계약 v1(신선도만)에 H6 검사의 **이름과 상태, 개수**를 더해 Claude 프로필에 넣는다.
  - 금액, 비중, 종목, 예외 키, 자유 문구는 나가지 않는다.
- **입출금은 지금 전부 사람이 닫는다.** Toss API에는 입출금 기록이 없다. 그래서 체결로 설명되지 않는 현금 변화(잔차)는 모두 예외가 되고, Thomas가 `--resolve`로 닫는다. 자동매매로 가려면 이 손작업이 없어야 한다.
  - "거래가 전혀 없는 창의 둥근 금액"만 규칙으로 입금·출금으로 닫는다.
  - 그 밖의 잔차(배당일 수 있는 것)는 지금처럼 사람이 본다.

## 1. 사실

### 1.1 Toss에는 입출금 기록 API가 없다 [확인]

- 2026-10-11 공식 스펙 v1.2.24 기록(`PORTFOLIO_AND_STOCK_TRADING_PLAN_AUDIT_V0.1.md` §D): "토스에는 현금 거래 API가 없다."
- 2026-10-26에 공개 문서를 다시 읽었다(`openapi-docs/latest/api-reference/README.md`, 엔드포인트 41개). 엔드포인트는 호출하지 않았다.
  - 입금, 출금, 이체, 배당, 거래내역, 현금 원장 엔드포인트가 없다.
  - 현금 쪽은 `GET /api/v1/accounts`, `/buying-power`, `/commissions`, `/orders`뿐이다.
- 그러므로 "API 기록을 근거로 입출금을 확인"하는 자동화는 Toss에서는 할 수 없다. 남는 근거는 창의 모양뿐이다: 거래가 있었는지, 결제가 있었는지, 금액이 어떤지.

### 1.2 지금 입금 하나가 하는 일 [확인, `holdings/cash_flows.py`]

1. `holdings_refresh` 사이의 창마다 현금 변화를 체결로 설명한다(`reconcile_window`).
2. 설명되지 않은 몫은 `toss_residual` 예외가 된다(`_toss_rows`, 1529행). 텔레그램에는 개수만 알린다(`store.py` 207행).
3. 예외가 열려 있는 동안 `post_cutover_exceptions`가 FAIL이고, `H6b_shadow_ready`가 막힌다.
4. Thomas가 `--resolve KEY --kind deposit`으로 닫으면 외부 흐름이 된다. 그 창은 매매 증거에서 빠지고, 그 창 끝까지 모은 증거도 무효가 된다(`toss_evidence`).

### 1.3 저장 스냅샷에 이미 있는 것 [확인, `store.py` 199–202행]

`snapshot.cash_flows.readiness` = `{checks, ready, shadow_days, shadow_success_dates, exceptions, next_step}`.
- `checks`는 12개 이름의 닫힌 목록이다: `binance_access`, `binance_real_schema`, `cross_source_dedupe`, `current_health`, `cutover_boundary`, `history_completeness`, `internal_transfer_guard`, `ledger_chain`, `ledger_state_consistency`, `post_cutover_exceptions`, `shadow_observation`, `toss_cash_semantics`.
- 값은 다섯 개다: `PASS`, `FAIL`, `WAITING`, `RUNNING`, `UNCHECKED`.
- `next_step`은 사람이 읽는 문구다.
- Toss 현금 검사가 무엇을 기다리는지(`toss_note`)는 스냅샷에 없다. 텔레그램 준비 알림에만 문구로 들어간다.

---

## 2. Claude read door: `holdings_status` 계약 v1.1

### 2.1 응답

§4.19.2의 v1(신선도)에 `h6` 블록 하나를 더한다. 원본 스냅샷이 아니라 허용 목록으로 새로 만든다. v1의 공통 규칙은 그대로다: 닫힌 스키마, 알 수 없는 값은 `UNKNOWN` + `v1-partial`, 고정된 오류 문구.

| 필드 | 값 | 출처 |
|---|---|---|
| `available`, `reason_code`, `snapshot_as_of`, `age_seconds`, `stale`, `partial` | v1 그대로 | v1 |
| `h6.ready` | true/false | `cash_flows.readiness.ready` |
| `h6.checks` | `{이름: 상태}`. 이름은 §1.3의 12개, 상태는 5개 값. 목록 밖 이름은 버리고 partial로 표시한다 | `cash_flows.readiness.checks` |
| `h6.open_exceptions` | 0 이상의 정수 | `cash_flows.readiness.exceptions` |
| `h6.shadow_days` | 0 이상의 수, 소수 첫째 자리 | `…shadow_days` |
| `h6.shadow_success_dates` | 0 이상의 정수 | `…shadow_success_dates` |
| `h6.toss_needs` (R3) | `["buy", "sell", "settlement_day"]`의 부분집합 | 새 구조 필드(§2.3) |

**나가지 않는 것:**
- 금액, 비중, 손익, 보유 수, 종목;
- `next_step`과 `toss_note` 문구;
- 예외 키(창 시각이 들어 있다), 계좌, `cash_flows`의 나머지;
- 스냅샷에 앞으로 생길 키.

### 2.2 경계

- **H3**(`holdings/disclosure.py`, "프로세스를 떠나는 표는 하나"): H3가 지키는 것은 종목별 몫이 드러나는 내역이다. `h6`에는 금액도 종목도 없고, 운영 검사의 이름과 상태만 있다. 그래서 H3의 "표"에 넣지 않고 운영 상태로 분류하는 것을 권고한다(R2).
  - 남는 노출: 예외 개수와 그 변화 시각. "언제 Thomas가 돈을 넣거나 뺐는가"가 대략 드러난다. 금액은 드러나지 않는다.
- **CLAUDE.md 가드레일:** "Claude reads the holdings lane only through its one table"은 바뀌어야 한다. 바꾼 뒤의 뜻: Claude는 그 표(Thomas 터미널)와 door의 `holdings_status` 투영만 읽는다. 금지 명령 목록은 그대로다. 이 문장은 Thomas의 거버넌스라 R2의 결정으로 고친다.
- **정책:** `assistant_read.verbs`에는 이미 `holdings_status`가 있다(1.6.2). Claude 프로필은 정책이 아니라 `read_bridge.PROFILES`(코드)가 정한다. 프로필에 넣는 일은 §4.19.3의 조건을 따른다: 구현, 시험, 결함 주입, 운영 검증 뒤 별도 승인.

### 2.3 `toss_needs` (R3)

- 지금 `toss_semantics`는 무엇이 모자라는지를 문구로만 돌려준다(`"epoch N: needs a buy, a sell"`).
- 문구를 파싱하지 않는다. `toss_evidence`의 세 불리언에서 `toss_needs` 목록을 만들어 `readiness`와 스냅샷에 넣는다(작은 코드 변경).
- 그러면 Claude가 "매수 1건이 아직 필요하다" 같은 상태를 Thomas 없이 알 수 있다.

### 2.4 시험

- **허용 목록:** 금액·비중·`next_step`·`toss_note`·예외 키·`cash_flows`의 다른 키를 원본에 주입해도 응답에 나오지 않는다(누출 주입).
- **알 수 없는 값:** 목록 밖 검사 이름과 상태는 `UNKNOWN` + partial로 처리한다. 스냅샷이 없을 때 v1처럼 `available:false`다.
- **닫힌 스키마:** `additionalProperties: false`이고, 패턴에 `\Z` 앵커를 쓴다.
- **프로필:** Claude 프로필의 읽기가 정확히 3개다(`schedules`, `scheduler_events`, `holdings_status`). 보류 읽기 3개는 그대로다.
- **운영 검증:** 배포 뒤 `claude`(2101) 계정에서 읽는다. 응답 키가 계약과 같아야 하고, 금액이 0개여야 한다.

---

## 3. Toss 입출금 자동 분류

### 3.1 선택지

| 안 | 내용 | 장점 | 비용·위험 |
|---|---|---|---|
| **A. 조용한 창 규칙 (권고)** | 체결·결제·환전·식별 변화가 없는 창의 KRW 잔차가 1,000원의 배수이면, 원장이 스스로 `deposit`(+) 또는 `withdrawal`(−)로 닫는다 | 사람 손이 없다. 근거가 기록된다(규칙 id, 창, 부호) | 둥근 금액의 배당·이자를 입금으로 잘못 닫을 수 있다(§3.3) |
| B. 미리 알린 흐름 | Thomas가 입출금할 때 컨트롤 봇에 `/flow deposit 200000`을 보낸다. 같은 금액의 잔차가 정해진 시간 안에 나오면 자동으로 닫힌다 | 정확하다 | 매번 Thomas 입력이 필요하다. 완전 자동이 아니다 |
| C. 지금처럼 | 모든 잔차를 Thomas가 `--resolve`로 닫는다 | 가장 보수적이다 | 입출금마다 손작업. 자동매매와 맞지 않는다 |
| D. 다른 브로커 | 입출금 기록 API가 있는 브로커 | 근거가 API에서 온다 | P3·D-A2 안의 큰 결정이다. "토스만"(2026-10-07)을 바꿔야 한다 |

### 3.2 A의 규칙 (`quiet_window.v1`)

다음을 모두 만족하는 잔차만 규칙이 닫는다. 하나라도 어긋나면 지금처럼 예외로 남는다.
1. 그 창에 매수·매도 체결이 없다.
2. 결제가 없다.
3. 환전 쌍이 없다.
4. 주문 조회가 잘리지 않았다.
5. 식별(`identity_stable`)이 안정이다.
6. KRW 주머니만 움직였고, USD는 0이다. 미국 ETF 배당은 USD로 들어오므로 USD 잔차는 규칙 밖이다.
7. 잔차의 절댓값이 1,000원의 배수이고 0이 아니다.

닫힐 때 동작:
- 원장에 `flow_resolved` 한 줄을 쓴다: `by: "rule:quiet_window.v1"`, `kind: deposit|withdrawal`(부호로 정함). 사람이 닫은 것과 구분된다.
- 그 창은 지금처럼 외부 흐름이다. 매매 증거에서 빠진다.
- 텔레그램 알림: "입금 1건 자동 분류" 식으로 방향과 개수만 보낸다. 금액을 넣을지는 F2에서 정한다.
- 되돌리기: Thomas가 `--resolve`로 다른 kind를 줄 수 있어야 한다. 그러려면 규칙이 닫은 것을 다시 여는 명령이 필요하다(새 코드, 같은 PR).

### 3.3 A가 틀리는 경우 [추정]

- **국내 배당·분배금:** 원천징수(15.4%) 뒤 금액이라 1,000원의 배수일 가능성이 낮다. 둥글게 떨어지면 입금으로 잘못 닫힌다.
  - 그러면 unit 회계에서 단위가 늘어 수익률이 그만큼 낮게 잡힌다(H6c 이후).
  - 지금 목표 배분에서 국내 배당 자산은 국내 주식 10%와 국고채 ETF 몫이다. 분배금은 연 몇 번이다.
- **수수료·세금 정산:** 대개 작은 비정수 금액이라 규칙 밖으로 남는다.
- **입금과 매수를 한 창에 한 경우:** 조건 1이 깨져 규칙 밖이고, 지금처럼 사람이 본다. Thomas에게 "입금 뒤 보드 시각이 한 번 바뀐 다음 매수"를 권한다.
- 틀리는 방향은 "자동으로 닫지 않음"(안전) 아니면 "작은 수익을 입금으로 봄"(성과가 조금 낮게 잡힘)이다. 돈이 빠져나가는 방향의 오류는 없다. 출금을 손실로 보는 반대 방향 오류도 조건 6·7로 줄어든다.

### 3.4 거버넌스

- `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`의 원칙("운영자가 봤다고 증거가 되지 않는다", 예외는 Thomas가 닫는다)을 바꾸는 일이다. 그래서 새 결정이다(F1).
- 규칙이 닫는 것은 Toss 잔차뿐이다. 바이낸스 쪽 예외(지급 미확인, 중복, 역전, 형식 오류)는 그대로 사람이 닫는다.
- H6c(unit 권위 전환) 전에는 이 분류가 판정에 쓰이지 않는다. 표시와 준비 상태에만 영향을 준다. 그래서 H6c 전에 몇 주 돌려 보고, 잘못 닫힌 것이 있는지 Thomas가 `--flows`로 확인할 수 있다.

---

## 4. 순서

1. **PR-1(R1–R3):**
   - `toss_needs` 구조 필드;
   - `holdings_status` v1.1 투영과 스키마;
   - Claude 프로필에 추가;
   - 시험;
   - CLAUDE.md 가드레일 한 줄(R2가 예일 때).
   - 병합 → 배포 1회 → 운영 검증.
2. **PR-2(F1–F2):** `quiet_window.v1` 규칙, 규칙이 닫은 것을 다시 여는 명령, 알림, 시험. 병합 → 배포 1회.
3. 두 PR은 서로 기다리지 않는다. PR-2가 먼저 들어가도 된다.

## 5. Thomas 결정 요청

| 결정 | 질문 | 권고 |
|---|---|---|
| **R1** | Claude read door에 `holdings_status` v1.1(신선도 + H6 검사 이름·상태·개수)을 넣는가 | **예** |
| **R2** | 이 투영을 H3의 "표"가 아닌 운영 상태로 보고, CLAUDE.md의 holdings 읽기 문장을 그에 맞게 고치는가 | **예.** 금지 명령 목록은 그대로 둔다 |
| **R3** | `toss_needs`(매수·매도·결제일 중 무엇이 남았는지)를 구조 필드로 더하는가 | **예** |
| **F1** | Toss 입출금을 어떻게 확인하는가: A 조용한 창 규칙 / B 미리 알린 흐름 / C 지금처럼 / D 다른 브로커 | **A.** 규칙 밖은 지금처럼 사람이 본다 |
| **F2** | 자동 분류 알림에 금액을 넣는가 | **아니오.** 방향과 개수만 보낸다(지금 예외 알림과 같은 수준) |

## 6. 이 문서가 하지 않는 것

- 코드, 정책, 스키마, CLAUDE.md를 바꾸지 않는다. 결정 뒤 §4의 PR로 한다.
- 보류 읽기 `runtime_status`, `heartbeat`, `crypto_readiness`는 다루지 않는다.
- H6b-shadow와 H6c의 승인은 다루지 않는다. 그것은 `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md`의 결정이다.
- Toss 주문 경로(P3)는 다시 열지 않는다.

## 결정 (Thomas 2026-10-26) — 권고대로

"승인: #1313 R1 예, R2 예, R3 예, F1 A, F2 아니오 / #1313 병합, PR-1·PR-2 구현 (Thomas 2026-10-26)"

- **R1 예:** Claude read door가 `holdings_status` v1.1을 답한다.
- **R2 예:** 그 검사 상태는 H3의 표가 아니라 운영 상태다. CLAUDE.md의 holdings 읽기 문장을 그에 맞게 고친다. 금지 명령 목록은 그대로다.
- **R3 예:** `toss_needs`를 구조 필드로 더한다.
- **F1 A:** `quiet_window.v1` 규칙으로 조용한 창의 둥근 KRW 잔차를 입금·출금으로 닫는다. 규칙 밖은 지금처럼 Thomas가 본다.
- **F2 아니오:** 자동 분류 알림에는 방향과 개수만 넣는다.
