# 제안: 선정 자동화는 라이브 문을 먼저 만들어야 가능하다 (DRAFT v0.1)

**상태:** DRAFT — 설계 선행(구현 아님). 코드 변경 없음. 정책 활성화 없음.
**성격:** 두 부분이며 방향이 반대다. **Part 1은 실제 돈 문을 좁힌다**(오늘 암묵적으로 열려
있는 것을 명시적 승인 뒤로 옮긴다). **Part 2는 돈에 닿지 않는 티어에 한정한 자동화**다.
**Part 2는 Part 1 없이는 착수할 수 없다** — 그 이유가 이 문서의 본론이다.
**측정 시각:** 2026-08-08T06:14–06:20Z, `thomas-scheduler` 컨테이너(실제 거래 프로세스) 및
`.runtime_governance_state/`.
**근거:** `governance/GOVERNANCE_POLICY.yaml`(v1.2.0), `docs/runtime-contracts/CRYPTO_PIPELINE_V0.1.md`,
`crypto/pool.py`, `crypto/lifecycle.py`, `crypto/live_entry.py`, `crypto/promotion.py`,
`docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md`.

> **이 문서는 착수 직전의 자기 전제를 정정한다. 그 정정이 제안의 형태를 결정했다.**
> 이 작업은 "임계값을 통과한 후보를 **페이퍼 풀까지만** 자동 설치하고 라이브 문은 지금처럼
> 수동으로 둔다"는 설계로 시작했다. **그런 라이브 문은 존재하지 않는다.** 전략 단위로 라이브
> 진입을 허가하는 관문은 이 런타임에 없고, 있었던 하나(Gate 0)는 2026-08-03에 —
> 충족 불가능하다는 정당한 이유로 — 제거됐다. 그래서 풀에 `PAPER_ACTIVE`로 설치하는 것은
> 곧 **라이브 라우팅 대상으로 무장하는 것**이다. "페이퍼 티어에만 자동 설치"는 그 티어를
> **먼저 만들어야** 성립하는 문장이었다.

---

## 0. 한 줄

**오늘 풀에 설치하는 것은 곧 실주문 자격을 주는 것이다.** 그러므로 선정 자동화의 선행 조건은
임계값 조정이나 승인 절차 완화가 아니라, **라우팅 티어를 둘로 쪼개어 "돈에 닿지 않는 자리"를
만드는 것**이다. 그 자리가 생긴 뒤에야 자동 설치는 승인 가능한 요청이 된다.

---

## 1. 측정 — 풀 설치가 곧 라이브 무장인 이유

거래 컨테이너에서 직접 판정(2026-08-08T06:14–06:20Z):

```
active_strategies              : 94        (SUSPENDED 89 / PAPER_ACTIVE 5)
routable_strategy_ids          :  5        ← OCCUPYING_STATUSES에서 파생
OCCUPYING_STATUSES             : {PAPER_ACTIVE, WARNING, PROBATION}    (paper.py:139)
pool updated_at                : 2026-07-31T10:04:33Z

live_trading_opt_in            : PASS      MVP_LIVE_TRADING=real
trading_armed                  : PASS
autonomous_routing_wired       : PASS      "a scheduled run can place live orders"
canary_evidence                : PASS      4/4
live_gate_recorded             : OPEN      2026-08-08T06:14:20Z (HELD)
=> READY
```

`live_entry`의 검사 순서는 route → verdict → reconciliation → capacity → filters → bracket →
economics → sizing → guard 이다(`live_entry.py:13–45`). **이 목록에 "이 전략이 라이브로
승인되었는가"를 묻는 항목은 없다.** 2b(Gate 0)가 그 자리였고 2026-08-03에 제거됐다 — 제거
자체는 옳았다(`GATE0_CANNOT_BE_SATISFIED_V0.1.md`: 유일하게 도달 가능한 상태가 "override"인
게이트였다). 그러나 제거의 부수 결과로 **전략 단위 라이브 관문은 0개가 되었다.**

남은 게이트는 전부 **능력(capability) 단위**다: 라이브 스위치, 카나리 4건, 예산 레코드, 킬
스위치, 주문 가드. 이들은 "이 런타임이 실주문을 낼 수 있는가"를 묻지 **"이 전략이 낼 자격이
있는가"를 묻지 않는다.** 따라서 새 전략이 `PAPER_ACTIVE`로 들어오는 순간, 그 전략은 다음
15분 사이클부터 실주문 후보다.

**오늘도 이미 그렇다.** 현재 라우팅 중인 5개(S008, S005-GEN-700, S004-GEN-706, S005-GEN-697,
S002-GEN-709)는 2026-07-31에 당시 저장된 `holdout=CONFIRMED / verdict=ROBUST` 라벨로
승격됐지만, **현행 규칙으로 재계산하면 5개 전부 `INSUFFICIENT / PROVISIONAL`** 이다(기간
슬라이스 확인 `MIN_HOLDOUT_PERIODS=8` 도입 이후). 즉 지금 실주문을 라우팅하는 전략들은
오늘의 승격 기준을 통과하지 못한다. Part 1은 자동화를 위한 준비이기 이전에 **이 노출에 대한
교정**이다.

---

## 2. 기존 계약이 금지하는 것, 그리고 이미 이름 붙여둔 유보 결정

`CRYPTO_PIPELINE_V0.1.md`는 두 가지를 명시한다.

**(a) 금지 — 구조적이다.**

> "a good backtest is **never** auto-promotion (`auto_promotion_allowed: false` in the source
> data becomes structural here); the active pool is a single pointer changed only through the
> approval door (the Core Release pointer precedent)."

Part 2는 이 문장과 정면으로 충돌한다. **그러므로 Part 2는 "구현되지 않은 기능의 구현"이
아니라 계약 개정 요청이다.** 이 문서가 요청하는 개정 범위는 **페이퍼 티어에 한정**되며,
라이브 라우팅 티어에 대해서는 위 문장을 **그대로 유지**한다(§4.4).

**(b) 유보된 결정 — 이미 이름이 있다.**

> "**Deferred decision (explicit, Thomas-only):** R10 consumption is currently scoped to
> `SENSITIVE_MEMORY_GOVERNANCE`. Strategy promotion would be the **second consumption scope**.
> Until that decision, promotion candidates + approval requests can exist end-to-end, but the
> final promotion remains an explicit operator action."

**이것은 우리가 원하는 것이 아니다.** R10 소비는 "Thomas가 건별로 승인하고, 그 승인을 운영자
CLI 대신 런타임이 소비한다"는 뜻이다. 사람이 건별로 승인하는 구조는 그대로다 — 자동 선정이
아니라 자동 *집행*이다. 이름이 비슷해 혼동되기 쉬우므로 명시한다: **R10 소비 확대는 이
제안의 대안이 아니며, 이 제안과 독립적으로 결정할 수 있다.**

---

## 3. Part 1 (선행, 필수) — 라우팅 티어 분리와 전략 단위 라이브 문

### 3.1 무엇을 바꾸는가

라우팅 자격을 **두 티어**로 쪼갠다.

| 티어 | 상태값 | 페이퍼/반사실 기록 | 실주문 | 진입 방법 |
|---|---|---|---|---|
| 관찰 티어 | `PAPER_ONLY` (신규) | O | **X** | 승격 문 (기존) 또는 §4의 자동 레인 |
| 라이브 티어 | `PAPER_ACTIVE` (기존) | O | O | **Thomas 승인 (신규 명시 관문)** |

- `OCCUPYING_STATUSES`(슬롯 점유·중복 방지·드로다운 귀속)에는 **두 티어 모두 포함**한다.
  관찰 티어 전략도 컨텍스트 슬롯을 점유하고 손실 귀속을 받는다 — 그러지 않으면 재승격으로
  드로다운을 세탁할 수 있다(`pool.py:1062–1082`의 기존 논거가 그대로 적용된다).
- `live_entry`는 **새 집합 `LIVE_ROUTABLE_STATUSES`** 를 읽는다. 관찰 티어는 여기 없다.
- 라이프사이클 사다리는 지금처럼 **강등만** 한다. 관찰 티어에서도 강등은 동작한다.

### 3.2 오늘 상태에 대한 효과 — 좁히는 변경이다

현행 5개는 개정 시점에 **관찰 티어로 내려간다.** 라이브 티어로 올리려면 Thomas의 명시적
승인이 필요하다. 이는 §1에서 측정한 노출(현행 규칙을 통과하지 못하는 증거 위에서 실주문이
라우팅되는 상태)을 **승인된 상태로 바꾸거나 종료시킨다.** 둘 중 무엇이든 지금보다 낫다.

**주의 — 마이그레이션은 진입이 아니라 정지다.** 개정 적용 시 열린 포지션이 있으면, 관찰
티어로 내려간 전략의 **청산 경로는 막지 않는다.** `evaluate_live_close_guard`가 reduceOnly
청산을 각종 차단에서 면제하는 이유와 동일하다 — 포지션을 가둔 정지는 정지가 아니다.

### 3.3 왜 새 관문을 만드는 것이 "Reuse first" 위반이 아닌가

승인 기구 자체는 **재사용**한다: `promotion.py`의 콘텐츠 해시 승인(`strategy_promotion.v2`),
`RUNTIME_GOVERNANCE` 스코프, 기존 `/approve` 채널. 새로 만드는 것은 관문의 *구현*이 아니라
**대상의 구분**(어느 상태값이 실주문에 닿는가)이다. 이는 새 Gate가 아니라 기존 Gate가 이미
가졌어야 할 해상도다.

---

## 4. Part 2 (본안) — 관찰 티어 자동 설치 게이트

### 4.1 형태

`GOVERNANCE_POLICY.yaml`의 `p5_policy_gate` 선례를 그대로 따른다: 조건을 **전부 열거**하고,
하나라도 없으면 거부하며, `gate_grants_authority: false`.

```yaml
# 제안 형태 — 승인 전에는 기록되지 않는다
strategy_observation_tier_autoinstall_gate:
  gate_id: thomas.crypto.observation_tier_autoinstall
  applies_to_scopes: [RUNTIME_GOVERNANCE]      # 스코프 신설 없음
  installs_into: PAPER_ONLY                    # 라이브 티어로는 절대 설치하지 않는다
  requires:
    - standing_authorization_record   # 버전·TTL·철회 가능, per-machine, 무결성 검사 (safety-flag grant 선례)
    - unchanged_selection_bar         # §4.2 — 기준은 낮추지 않는다
    - observation_tier_only           # 설치 대상 상태값이 PAPER_ONLY가 아니면 거부
    - pool_and_slot_caps_unchanged    # 기존 풀 크기·컨텍스트 슬롯 상한 그대로
    - install_rate_limit              # §4.3
    - runtime_kill_switch_active      # 기존 킬 스위치가 이 레인도 막는다
    - post_action_report_and_audit    # 수동 문과 동일한 콘텐츠 해시로 건별 감사 기록
  gate_grants_authority: false
```

### 4.2 기준은 **낮추지 않는다** — 이것이 이 제안의 핵심 제약이다

자동 레인이 사용하는 판정은 현행 `promotable_backlog`의 것과 **글자 그대로 동일**하다:
`verdict == ROBUST`, `holdout_status == CONFIRMED`(OOS 25거래 + 1.96σ + 10개 기간 슬라이스 중
8개 t-검정), 선정 보정(Bonferroni) 등급, 현행 비용 기준 기대값 > 0, 라이프사이클 창 조건.

**자동화가 유혹하는 방향은 정확히 이 바를 낮추는 것이고, 그것은 거부한다.** 그 바는 다중검정
때문에 존재한다. 낮추는 순간 이 레인은 p-해킹을 자동화하는 장치가 된다.

**오늘 기준 이 레인은 아무것도 설치하지 않는다.** 저장된 1,801개 후보 중 현행 홀드아웃
규칙을 통과하는 것은 **0개**다(2026-08-08 재계산). 이는 두 가지를 뜻한다 — (i) 승인해도
즉각적 위험이 없다, (ii) **승인해도 즉각적 이득도 없다.** 이 레인의 값어치는 생성기가
무언가를 만들어낸 뒤에 발생한다. 그 전에 만들어 두는 이유는 §6이다.

### 4.3 유입 속도 제한

규칙 변경이나 데이터 이상으로 다수 후보가 동시에 바를 통과하는 경우를 대비해, **발화당 1건 /
일당 2건**을 상한으로 제안한다. 상한 초과분은 버려지지 않고 다음 발화로 이월되며, 초과
사실이 기록된다(`log()`되지 않는 절삭은 "전부 처리됨"으로 읽힌다).

### 4.4 유지되는 것

- **라이브 티어 진입은 영원히 수동이다.** `CRYPTO_PIPELINE_V0.1.md`의 "never auto-promotion"
  문장은 라이브 티어에 대해 **원문 그대로 유지**되며, 개정문은 그 문장의 적용 범위를
  라이브 티어로 명시하는 형태여야 한다(삭제가 아니라 한정).
- 자동 재활성화는 없다. `SUSPENDED`/`ARCHIVED`는 계속 종착 상태다.
- 안전 플래그, 예산, 카나리, 킬 스위치, 주문 가드 — 전부 그대로.
- Claude는 이 레인을 켜지 않는다. 켜는 것은 Thomas의 서명된 standing authorization이다.

---

## 5. 실패 방향

| 상황 | 동작 |
|---|---|
| standing authorization 없음/만료/무결성 불일치 | 자동 설치 없음 (수동 문은 그대로 동작) |
| 후보 저장소·풀 파일 판독 불가 | 자동 설치 없음 (BLOCK, 사유 코드) |
| 판정 입력이 애매(라벨 vintage 불일치 등) | 자동 설치 없음 — 재계산 결과만 신뢰 |
| 킬 스위치 활성 | 자동 설치 없음 |
| 상한 초과 | 이월 + 기록 |
| 설치 후 성과 악화 | 기존 라이프사이클 사다리가 자동 강등 (관찰 티어에서도 동작) |

전부 "설치하지 않음"으로 수렴한다. 이 레인의 실패는 **아무 일도 일어나지 않는 것**이어야 하며,
잘못된 설치여서는 안 된다.

---

## 6. 왜 지금 하는가 — 그리고 왜 이것이 급하지 않은가

**지금 하는 이유:** 선정 문은 2026-07-31 이후 0건을 통과시켰고, 그동안 생성은 하루 60–80개씩
계속됐다. 생성기가 무언가를 만들어냈을 때 그것을 흡수할 구조가 없으면, 사람이 매번 눌러야
하는 병목이 그대로 재현된다. 그리고 Part 1은 자동화와 무관하게 **오늘 존재하는 노출**을
교정한다.

**급하지 않은 이유:** §4.2에서 측정했듯 오늘 통과자는 0명이다. 이 제안을 승인해도 관찰
티어는 비어 있고, 반려해도 잃는 것은 없다. **급한 것은 Part 1이지 Part 2가 아니다.**

---

## 7. 승인 시 필요한 변경 (파일 단위)

**Part 1**
- `crypto/lifecycle.py` — `PAPER_ONLY` 상태 추가, 사다리 순위 편입, 종착 상태 규칙 유지
- `crypto/paper.py` — `OCCUPYING_STATUSES`에 관찰 티어 포함
- `crypto/pool.py` — `LIVE_ROUTABLE_STATUSES` 신설, `routable_strategy_ids` 의미 분리
- `crypto/live_entry.py` — 라이브 티어 검사 1항목 추가(제거된 2b 자리와 다른 성격: 능력이 아니라 자격)
- `crypto/promotion.py` + `scripts/promote_strategy_candidates.py` — 승격 시 목표 티어 인자, 콘텐츠 해시에 티어 포함(`strategy_promotion.v3`)
- 마이그레이션: 현행 5개를 관찰 티어로, 열린 포지션 청산 경로 보존
- `docs/runtime-contracts/CRYPTO_PIPELINE_V0.1.md` — 티어 정의 추가

**Part 2**
- `governance/GOVERNANCE_POLICY.yaml` — 게이트 정의 추가(policy_version bump)
- `docs/runtime-contracts/CRYPTO_PIPELINE_V0.1.md` — "never auto-promotion"의 적용 범위를 라이브 티어로 한정
- standing authorization 레코드 스키마 + 검증기 (safety-flag grant 재사용 검토 우선)
- 스케줄러: 자동 설치 잡, 상한, 감사 기록
- 테스트: 실패 방향 6종 전부, 티어 격리(관찰 티어 전략이 `live_entry`에 도달하지 않음)

---

## 8. 반대 논거와 미해결 질문

**반대 1 — "티어를 늘리면 상태 기계가 복잡해진다."** 타당하다. 다만 대안은 "설치 = 실주문
자격"이라는 현 상태이며, 그것은 단순한 게 아니라 **구분이 없는 것**이다. Gate 0 제거가
남긴 공백을 메우는 최소 형태가 티어 하나다.

**반대 2 — "관찰 티어는 결국 두 번째 승인 단계다. 병목이 사라지지 않는다."** 사실이다.
사라지는 것은 *생성→관찰* 병목이고, *관찰→라이브* 병목은 의도적으로 남긴다. 자동화의 대상은
"증거를 더 모을 자격"이지 "돈을 쓸 자격"이 아니다.

**반대 3 — "통과자가 0명인데 왜 만드나."** §6. 그리고 Part 1은 통과자 수와 무관하다.

**미해결 A — 관찰 티어의 증거는 라이브 자격의 근거가 되는가?** 관찰 티어에서 쌓는 것은
페이퍼·반사실 기록이다. 이 저장소는 페이퍼 성과가 라이브 문의 근거가 될 수 있는지에 대해
이미 부정적 측정을 가지고 있다(Gate 0의 폐기 경위). **이 문서는 그 질문에 답하지 않으며,
관찰 티어를 "라이브 승급 대기열"로 정의하지도 않는다.** 그것은 별도 결정이다.

**미해결 B — 관찰 티어 전략도 슬롯을 점유하므로, 자동 설치가 라이브 티어 후보의 슬롯을
잠식할 수 있다.** 컨텍스트당 슬롯 상한을 티어별로 분리할지, 공유할지는 미정. 기본값은
**공유 + 라이브 티어 우선**을 제안하나 근거가 약하다.

**미해결 C — standing authorization의 TTL.** 안전 플래그 grant의 30일 상한은 무인 운영과
충돌한 전례가 있다(P5 게이트에서 그 이유로 제거됨). 이 레인은 포지션을 가두지 않으므로 같은
문제는 없으나, 만료가 조용한 정지로 읽히지 않도록 만료 임박 보고가 필요하다.

---

## 9. 요청

1. **Part 1을 먼저 승인/반려해 달라.** 자동화와 분리해서 판단 가능하며, 단독으로도 오늘의
   노출을 교정한다.
2. Part 2는 Part 1이 병합된 뒤 별도 승인 대상으로 둔다.
3. Part 2를 승인하지 않기로 결정하는 경우에도 §4.2의 기준(바를 낮추지 않는다)은 향후 어떤
   자동화 제안에도 적용되는 제약으로 남기기를 제안한다.
