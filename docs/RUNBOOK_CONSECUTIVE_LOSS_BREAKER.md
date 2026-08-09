# 복구 절차서 — 연속손실 브레이커 (RUNBOOK)

**성격:** 현재 동작을 기술한 운영 절차서. 코드 변경 없음. **이 문서는 권한을 부여하지 않는다** —
§5의 재개는 실제 돈 문을 다시 여는 결정이며 Thomas의 것이다. 여기 적힌 것은 그 결정을 시간
압박 없이 내리기 위한 준비다.
**검증:** 이 문서의 모든 명령은 2026-08-08에 `thomas-scheduler` 컨테이너에서 실제로 실행해
확인했다. 상태를 바꾸는 명령(§5)은 실행하지 않고 `--help`와 `--show`로만 확인했다.
**작성 계기:** 2026-08-08 기준 `consecutive_losses` 2 / 한도 3. 라이브 청산 2건이 모두 손절이며
한 번 더 지면 발동한다.

---

## 0. 한 줄

**연속손실 브레이커는 걸리면 스스로 풀리지 않는다.** streak를 끊으려면 라이브에서 이겨야
하는데, 걸리면 라이브 진입이 막혀서 이길 기회가 생기지 않는다. 다른 브레이커에는 전부 풀리는
길이 있고 이것만 없다.

---

## 1. 걸리면 무엇이 멈추는가 — 그리고 무엇이 계속되는가

| | 상태 |
|---|---|
| 라이브 신규 진입 | **멈춘다** |
| 라이브 포지션 청산 | 계속된다 (막지 않는다) |
| 페이퍼 신규 진입·정산 | **계속된다** |
| 데이터 수집 / 팩토리 민팅 / 반사실 장부 | 계속된다 |

페이퍼가 계속되는 것은 우연이 아니다. 페이퍼 레그는 `paper_trade_verdict(health)`
(`cycle.py:618`)로 판정하며 **데이터 건전성만** 본다 — 손실 브레이커를 보지 않는다.
`guards.paper_trade_verdict` docstring이 그 이유를 기록한다: 과거에 페이퍼를 같은 브레이커로
재던 결과 86건이 정산되는 동안 656건이 막혔고, 라우팅 전략들의 표본이 사다리 창에 닿지 못했다 —
*"자신을 풀어줄 증거를 억누르는 한도는 브레이크가 아니라 래치다."*

**따라서 브레이커가 걸린 동안에도 증거는 계속 쌓인다.** 이것이 §6의 "재개하지 않기"를 실질적인
선택지로 만든다.

---

## 2. 걸렸는지 확인 (전부 읽기 전용)

```bash
docker exec thomas-scheduler python -m runtime.mvp_runtime.crypto.live_readiness
```

```bash
docker exec thomas-scheduler python -m runtime.mvp_runtime.console_cli status
```

```bash
docker exec thomas-scheduler python -m scripts.register_crypto_risk_limits --show
```

판정 신호:

- `breaker_watch_mark.json` → `status: BLOCK_NEW_POSITION`, `allow_new_position: false`,
  `consecutive_losses >= 한도`
- `live_readiness` → 해당 브레이커 항목이 FAIL
- `console_cli status` → `mode: ACTIVE`인데도 라이브 진입이 안 나가면 브레이커 쪽이다
  (킬 스위치와 구분되는 지점: 킬 스위치면 `mode`가 ACTIVE가 아니다)

---

## 3. 왜 스스로 풀리지 않는가

`_consecutive_losses`(`guards.py:287`)는 **가장 최근 라이브 종료 결과부터 거꾸로** 세다가 손실이
아닌 결과를 만나면 멈춘다. **시간 창이 없다** — 일일·주간이 `_pnl_since`로 당일/당주 시작
이후만 보는 것과 다르게, 이것은 전체 라이브 이력을 본다.

| 브레이커 | 풀리는 길 |
|---|---|
| 일일 / 주간 | 시간 (롤링 창) |
| 드로다운 | 회복 — *"unlatches when equity recovers to a new peak"* (명시적 설계) |
| **연속손실** | **라이브 승리뿐 — 그리고 걸리면 그 기회가 없다** |

이 비대칭이 의도인지는 코드 어디에도 적혀 있지 않다. 결함으로 단정하지 않는다(엣지가 증명되지
않은 시스템에서 3연패 후 "멈추고 사람을 부른다"는 타당한 자세다). 다만 **"당분간 멈춤"이 아니라
"사람이 풀 때까지 멈춤"** 이라는 점이 이 절차서의 존재 이유다.

---

## 4. 재개를 판단하기 전에 볼 것 — 건너뛰지 말 것

브레이커는 "무언가 잘못됐다"고 말할 뿐 무엇이 잘못됐는지는 말하지 않는다. 네 가지를 먼저 본다.

**(a) 3연패가 한 계보인가, 여러 계보인가.**

```bash
docker exec thomas-scheduler python -c "
import json
for l in open('/app/.runtime_governance_state/crypto/live_outcomes.jsonl'):
    r=json.loads(l)
    print(r['closed_at_utc'], r['symbol'], r['strategy_id'], r['result_R'], r['close_reason'])
"
```

한 계보에 몰려 있으면 그 계보의 문제이고, 계보별 대응(→ `EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md`)이
필요한 상황이다. 흩어져 있으면 시장 국면이나 비용 구조의 문제이며, 한도를 푸는 것은 답이 아니다.

**(b) 실현 R이 계획 R보다 큰가.** `result_R`이 −1.0보다 큰 절댓값이면 손절이 계획대로 체결되지
않았다는 뜻이다(2026-08-04 ETHUSDT 건: **−1.108R**). 이것이 반복되면 브레이커·허용치·사다리를
포함한 **모든 R 기반 한도가 체계적으로 낙관적**이라는 뜻이고, 그 상태에서 한도를 푸는 것은
실제로는 생각보다 더 크게 푸는 것이다.

**(c) 다른 브레이커의 여유.** 연속손실만 걸리고 주간·드로다운에 여유가 있다면 국지적 사건일
가능성이 있다. 여러 개가 동시에 압박받고 있으면 그렇지 않다.

**(d) 데이터와 거래소 정합성.** `live_readiness`의 reconciliation·market_data 항목. 장부가 거래소와
어긋난 상태에서 재개하면 브레이커가 막아준 것을 되돌리는 것이다.

---

## 5. 재개하는 유일한 경로 — 그리고 그 의미

**in-system 경로는 `max_consecutive_losses`를 올리는 것 하나뿐이다.** 그리고 그것은 정확히 방금
발동한 그 한도를 푸는 일이다. 그래서 이 절차의 무게는 명령이 아니라 §4에 있다.

**+1과 짧은 유효기간은 "1회 탐침"이다.** 한도를 3→4로 올리면 streak 3은 통과하지만, 다음 라이브
결과가 또 손실이면 4에서 즉시 다시 걸린다. 즉 **라이브 거래 딱 한 번을 허용하는 것**이고, 그
결과가 승리면 streak는 0으로 리셋된다. 유효기간이 지나면 기록은 만료되어 기본값(3)으로
돌아간다 — **되돌리는 것을 잊어도 안전한 방향으로 만료된다.**

```bash
# Thomas가 실행. 값과 유효기간은 §4의 판단에 따라 정한다.
docker exec thomas-scheduler python -m scripts.register_crypto_risk_limits \
  --registered-by thomas \
  --risk-per-trade 0.01 --daily-max-loss-r -2.0 --weekly-max-loss-r -5.0 \
  --max-consecutive-losses 4 --max-drawdown-pct -10.0 \
  --valid-days 3
```

**다섯 개 한도를 전부 명시한 이유 — 이것이 이 절차서에서 가장 사고 나기 쉬운 지점이다.**
미지정 항목은 **`guards.py`의 기본값으로 기록된다**(생략이 아니라 기본값 기입). 2026-08-08 현재는
등록된 레코드가 없어 실효값이 전부 기본값이므로 무해하지만, 언젠가 어떤 한도를 **조여** 둔
상태에서 하나만 지정해 등록하면 **조여둔 나머지가 조용히 풀린다.** 반드시 `--show`로 현재
실효값을 먼저 읽고, 다섯 개를 전부 명시할 것.

기타 제약:

- 코드 상한은 `MAX_MAX_CONSECUTIVE_LOSSES = 10`. 그 이상은 코드 변경 + 배포 사안이다.
- 범위를 벗어난 값은 무시되거나 clamp되지 않는다 — **레코드 전체가 거부되어 fail-closed**가 된다.
- 반영은 다음 사이클(15분 주기)에 이루어진다. 재시작은 필요 없다.
- 레코드는 자기 해시로 검증된다. 판독 불가·해시 불일치·만료는 전부 기본값 복귀가 아니라 **BLOCK**이다.

---

## 6. 재개하지 않는 것도 결정이다 — 그리고 아마 기본값이다

아무것도 하지 않으면 라이브는 멈춘 채로 있고 페이퍼는 계속 돈다(§1). 현재 풀 상태 —
승격 문 통과 0건, 라우팅 5개 계보의 페이퍼 증거 합계 5건, 사다리는 첫 등급에도 도달 불가 —
에서는 **라이브를 멈춘 채 증거를 쌓는 것이 합리적인 기본값일 수 있다.**

다만 "멈춰 있음"이 방치가 아니라 선택이 되도록, 재개하지 않기로 한 결정도 근거와 함께 기록할
것. 그러지 않으면 며칠 뒤 라이브가 조용히 죽어 있는 상태를 아무도 설명하지 못한다.

---

## 7. 하지 말 것

- **`live_outcomes.jsonl`을 편집해 streak를 끊는 것.** 레코드는 자기 해시로 검증되며 변조로
  읽힌다. 그리고 판독 불가는 기본값 복귀가 아니라 fail-closed다 — 더 나빠진다.
- **카나리로 streak를 끊으려는 시도.** 카나리는 `live_canary_orders.jsonl`에 기록되며
  `live_outcomes.jsonl`과 다른 저장소다. streak에 아무 영향이 없다.
- **`MVP_LIVE_TRADING`을 해제해서 멈추는 것.** 재시작이 필요하고 **청산 경로까지 닫는다.**
  즉시 멈춰야 하면 킬 스위치를 쓴다(청산은 열어 둔다):
  ```bash
  docker exec thomas-scheduler python -m runtime.mvp_runtime.console_cli kill --reason "..."
  ```
- **호스트에서 root로 상태 기록 CLI를 실행하는 것.** 서비스는 uid 10001로 돌며, root가 만든
  파일은 이후 서비스가 쓰지 못한다. 반드시 `docker exec ... python -m scripts.<name>`
  **모듈 형식**을 쓴다(`python scripts/<name>.py`는 `sys.path` 문제로 실패한다).
- **한도를 올려 재개한 뒤 §4를 사후에 하는 것.** 진단 없이 푸는 것은 브레이커를 끄는 것과 같다.

---

## 8. 기록

재개하든 하지 않든 근거를 남긴다.

- 재개: 한도 레코드의 `registered_by`와 유효기간이 그 자체로 감사 흔적이다. 무엇을 근거로
  풀었는지는 별도로 남길 것.
- 미재개: `console_cli --reason`에 사유를 담아 상태를 기록하거나, 결정을 문서로 남길 것.

---

## 9. 관련 문서

- `docs/proposals/EVALUATION_CANNOT_ACT_PER_STRATEGY_V0.1.md` — 계보별 라이브 허용치 제안.
  채택되면 3연패가 풀 전체가 아니라 해당 계보 하나만 멈추게 된다. §8-D가 이 래치를 다룬다.
- `docs/proposals/AUTOMATIC_SELECTION_NEEDS_A_LIVE_DOOR_V0.1.md` — 전략 단위 라이브 관문 부재.
- `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md` — 충족 불가능한 게이트의 선례.
