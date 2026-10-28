# 제안: H6b-shadow — unitized NAV를 옆에서만 계산한다 (DRAFT v0.1)

**상태:** DECIDED 2026-10-28 — S1=A, S2=A, S3=A, S4=A, S5=A(Thomas, "승인: H6b-shadow S1=A S2=A S3=A S4=A S5=A, 설계 문서·구현·PR"). 같은 날 PR 하나로 지었다(`holdings/unit_shadow.py`, 아래 §3). 배포 전에는 아무것도 계산하지 않는다. H6c는 이 문서가 승인하지 않는다.

**대상:** `PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md` §7의 3단계(H6b-shadow). 그 문서의 D-H6-4(시작 100), D-H6-8(사건별 발행·소각),
D-H6-9(shadow는 판정에 쓰지 않는다)와 §4(unit 회계)는 이미 결정됐다. 여기서는 그 결정이 정하지 않은 다섯 가지만 묻는다.

**기준 시점:** 2026-10-28, origin/main `127afa71`. [확인]은 코드로 확인한 것이고, [추정]은 계산하거나 짐작한 것이다.

---

## 0. 한 줄

입금은 unit 발행, 출금은 unit 소각이다. NAV/unit과 그 고점 대비 낙폭을 지금의 낙폭 판정 **옆에서만** 계산해 보이고,
실제 흐름을 겪은 뒤 H6c(권위 전환)를 묻는다. 지금의 낙폭 판정, `--reset-peak`, 알림은 바뀌지 않는다.

## 1. 지금 있는 것과 없는 것

- [확인] 완전 NAV는 매 실행 `combined.combine`이 만든다(`portfolio_nav_complete`, 범위 v1). 고점은 `holdings_peak.json` 하나다.
- [확인] **NAV의 시점별 기록은 없다.** 스냅샷과 로컬 파일은 최신 1개만 남긴다. 기준선 기록(`holdings_baselines.jsonl`)은 고점이 바뀔 때만 쓴다.
- [확인] 흐름은 원장(`holdings_cash_flows.jsonl`)에 있다.
  - 바이낸스: `flow_recorded`(CONFIRMED). 자산과 수량만 있고 원화 가치는 없다(`valuation_status: UNVALUED`).
    - cutover 뒤의 사건만 `ELIGIBLE`이다.
    - 카드·Pay 결제는 HELD다.
  - 토스: `toss_residual`. 원화·달러 잔차 금액이 있다. 이것을 `flow_resolved`(Thomas 또는 `rule:quiet_window.v1/v2`)가 deposit / withdrawal / income / fee / read_incomplete / semantics_mismatch로 닫는다.
- [확인] 연쇄 해시 기록 도구 `holdings/chained_log.py`가 있다(기준선 기록이 쓴다).
- [확인] 2026-10-28 현재 H6 준비 검사는 12개 중 11개가 PASS다. `toss_cash_semantics`가 매도 1건과 깨끗한 결제일 1일을 기다린다(`toss_needs: sell, settlement_day`).

## 2. 결정할 것

### S1. shadow를 언제 시작하나

- **A (권고):** H6 준비(`H6b_shadow_ready`)가 true가 된 뒤 첫 완전 NAV에서 NAV/unit = 100으로 시작한다. §7 순서와 준비 신호의 이름 그대로다.
  - 준비 전의 토스 잔차는 D-H6-7 gate 아래에서 `SHADOW_ONLY`이므로, 그 의미가 검증되기 전의 흐름으로 unit을 만들지 않는다.
- B: 배포 직후 첫 완전 NAV에서 시작한다. 관찰 기간이 며칠 늘지만, 의미가 검증되지 않은 토스 흐름이 섞인다.
- 어느 쪽이든 NAV 시점 기록(S4)은 배포 때부터 쌓는다. S4=A이면 시작점은 나중에 다시 정할 수 있다(전체를 다시 계산하므로).

### S2. 무엇이 unit을 움직이나

- **A (권고):** 외부 입금·출금만 움직인다.
  - 바이낸스: cutover 뒤 `ELIGIBLE`이고 READY인 `flow_recorded`. in은 발행, out은 소각이다.
  - 토스: deposit 또는 withdrawal로 닫힌 `toss_residual`. 사람이 닫았는지 규칙이 닫았는지는 가리지 않는다.
  - 다음은 unit을 움직이지 않는다. NAV/unit의 변화, 곧 성과로 남는다.
    - income(배당·이자)과 fee: 계좌가 번 것과 쓴 것이다.
    - read_incomplete와 semantics_mismatch: 돈이 들고 나지 않았다.
    - excluded, 내부 이체, 거래소 내 환전.
- B: income과 fee도 흐름으로 본다(§3 원장 설계가 쓴 종류 목록 그대로). 그러면 배당이 성과에서 빠진다.

### S3. 흐름의 원화 가치

- **A (권고):** 흐름을 반영하는 그 실행의 NAV와 같은 가격으로 매긴다. 그래야 흐름이 NAV/unit을 움직이지 않는다.
  - 바이낸스 USDT·USDC·FDUSD: 1달러로 보고, 그 실행의 토스 매매기준율을 쓴다(P2와 같다).
  - 그 밖의 코인: 그 실행 지갑 읽기의 가격을 쓴다. 가격이 없으면 그 흐름은 HELD이고, NAV/unit과 판정이 보류된다(D-H6-6).
  - 토스: 잔차 금액 그대로 쓴다. 달러 잔차는 그 실행의 환율로 바꾼다.
    - 규칙 v2가 닫은 입금도 반올림한 금액(`rule_amount_krw`)이 아니라 잔차를 쓴다. 차이는 1,000원 이하이고 unit 쪽으로 들어간다. [추정: 한 건 오차가 NAV의 0.01% 미만]
- B: 바이낸스 흐름도 사건 시각의 가격으로 매긴다. 과거 가격 조회라는 새 네트워크 읽기가 필요하고, 그 읽기는 env-only gate를 거쳐야 한다. 권고하지 않는다.

### S4. 보류와 늦은 확정을 어떻게 처리하나

- **A (권고):** 완전 NAV를 실행마다 한 줄씩, 새 로컬 연쇄 기록에 남긴다.
  - 파일은 `holdings_nav_points.jsonl`이고 `chained_log`를 재사용한다.
  - 금액이 들어가므로 LOCAL_ONLY다. 어떤 도어도 읽지 않으며, 기준선 기록과 같은 고정 테스트를 둔다.
  - shadow는 매 실행 시작점부터 다시 계산한다(순수 함수: NAV 시점 + 흐름 → unit 경로).
  - 흐름은 사건 시각 뒤의 첫 NAV 시점에 붙는다(`hourly_approximation_v1`, §4). 미해결이나 HELD가 있으면 그 시점부터 NAV/unit이 직전 값에 머문다.
  - Thomas가 나중에 확정하면, 다음 재계산에서 그 흐름의 시점에 들어간다. 설계 §4의 "발생 시각 기준으로 다시 계산"이 그대로 된다.
  - shadow는 원장에 `units_changed` 줄을 쓰지 않는다. 그 줄은 H6c에서 권위가 넘어갈 때부터 쓴다.
  - [추정] 기록은 1년에 약 8,800줄이고, 재계산은 실행당 수 ms다.
- B: 상태 파일에 unit 수와 고점만 증분으로 둔다. 늦게 확정된 흐름은 확정 시점의 NAV/unit으로 처리한다. 오차는 "흐름 금액 × 미해결 기간의 수익률"이다. 파일은 작지만 §4를 근사한다.

### S5. 어디에 보이고, H6c는 언제 묻나

- **A (권고):**
  - 보드(Thomas의 표)에 `unit shadow` 줄을 둔다. NAV/unit, unit 고점 대비 낙폭, 반영한 입금·출금 건수, 보류 건수를 보인다. 금액은 없다.
  - Claude 읽기 도어 `holdings_status`에는 shadow 상태만 더한다. 계약은 v1.2가 된다.
    - 더하는 것: 시작 여부, 시작 뒤 일수, 반영한 흐름 수, 보류 수.
    - NAV/unit과 낙폭은 넣지 않는다.
  - 텔레그램 알림은 없다(D-H6-9: 판정에 쓰지 않는다).
  - **H6c는 네 조건이 모두 갖춰지면 묻는다.** 조건이 갖춰지면 도어의 shadow 상태에 `h6c_askable`이 서고, Claude가 결정 패킷을 낸다.
    1. shadow 14일이 지났다.
    2. 실제 입금 1건 이상과 실제 출금 1건 이상이 반영됐다.
    3. 그 시점에 보류가 0건이다.
    4. 원장·NAV 기록의 사슬이 온전하다.
  - 출금은 드물다. 그래서 소액 출금 1건(예: 토스 → 은행 1만 원)을 Thomas가 한 번 해야 할 수 있다.
- B: 보드에만 보이고, H6c는 Thomas가 원할 때 묻는다.

## 3. 짓는 것 (S1–S5가 모두 A일 때)

PR 하나로 짓는다. 배포는 Thomas가 평소의 배포 스크립트로 한다.

- `holdings/unit_shadow.py`(구현에서 NAV 기록과 재계산을 한 모듈로 합쳤다):
  - 완전 NAV 한 줄을 추가한다. 연쇄 해시이고 LOCAL_ONLY다.
  - NAV 시점과 원장으로 unit 경로를 다시 계산한다.
  - 내놓는 것: NAV/unit, unit 고점, 낙폭, 반영 건수, 보류와 그 이유, `h6c_askable`.
- `store`: 매 실행 NAV 시점을 남기고 shadow 블록을 계산한다. shadow가 실패해도 지금의 판정과 알림은 그대로 돈다(실패는 블록의 상태로 남는다).
- 보드 줄을 더한다. `store_reads.project_holdings_status` v1.2를 만든다. 계약 문서를 고친다.
- 시험:
  - 같은 흐름을 두 번 읽어도 발행은 한 번이다.
  - 입금·출금 직후 NAV/unit이 흐름 때문에 움직이지 않는다.
  - income·fee는 성과다.
  - HELD·미해결 동안 NAV/unit이 머물고, 확정 뒤 그 시점으로 다시 계산된다.
  - 가격이 없는 코인은 HELD다.
  - 도어와 반출 경로가 NAV 기록을 읽지 않는다.
  - 지금의 낙폭 판정과 고점 파일이 바뀌지 않는다.
  - 돌연변이 검사를 한다.
- 이력 노트를 쓰고, 원장 제안서의 상태 줄을 고친다.

## 4. 남는 위험

- **시각 근사:** 한 시간 단위 근사다(§4 `hourly_approximation_v1`). 1천만 원 입금과 그 시간 1% 변동이면 약 10만 원이 기존 unit에 귀속된다. [추정, §4의 계산]
- **금액 파일:** NAV 기록은 금액이 있는 로컬 파일이 하나 더 생기는 것이다. 경계는 기준선 기록과 같다. 도어 고정 테스트와 Claude 읽기 금지 규칙(CLAUDE.md)이 그대로 적용된다.
- **출금 조건:** 출금이 없으면 S5의 조건 2가 채워지지 않는다. H6c는 그만큼 늦어진다.

## 5. 승인 문구

```text
승인: H6b-shadow S1=A S2=A S3=A S4=A S5=A, 설계 문서·구현·PR (Thomas 2026-10-28)
```
