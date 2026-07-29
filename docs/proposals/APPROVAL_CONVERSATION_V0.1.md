# 제안: 승인 대화 — "승인"이라고 말하면 승인될 것인가 (DRAFT)

> **상태: DRAFT — 결정을 위한 문서. 어떤 것도 활성화하지 않으며 구현 착수도 아니다.**
> 요구되는 Thomas 결정은 §6에 있다.
>
> 사실 확인 기준: `main` = `a18615e` (2026-07-28). §1의 정책 인용은 전부
> `governance/GOVERNANCE_POLICY.yaml`에서 직접 읽었다. 이 문서는 정책의 authority가 아니다 —
> 정책을 **바꾸자는 제안**이며, 바꿀지는 Thomas가 정한다.

---

## 1. 지금의 규칙 — 우연이 아니라 결정이다

```yaml
control_channel:
  approval_id_or_fingerprint_code_required: true
  explicit_approval_expression_required: true
  invalid_approval_sources:
    - telegram_group
    - telegram_channel
    - other_user
    - forwarded_message
    - emoji_only
    - ambiguous_expression          # ← 자연어 "승인"이 여기 걸린다
    - stale_message_without_matching_code
    - different_action
approval_lifetime:
  one_time_use_required: true
  approval_reuse_allowed: false
  default_approval_ttl_minutes: 30
```

그리고 프론트데스크 역할 계약의 `unsupported_capabilities`에는
`approval_issuance_or_consumption`이 있다 — **코드 경로도 없고 스키마 필드도 없다.**

즉 오늘 승인은 `/approve <approval_id>` 하나뿐이고, 그건 네 겹으로 고정되어 있다:
정책 2줄 + 무효 출처 목록 + 역할 계약 + 스키마의 부재.

## 2. 그래서 무엇이 불편한가

승인이 필요한 실제 작업은 지금 셋이다 — 메모리 승격(`SENSITIVE_MEMORY_GOVERNANCE`),
후보 역할 시험(`CANDIDATE_ROLE_TRIAL`), 프로그램 등록(`TOOL_PROGRAM_GOVERNANCE`).
전부 ORANGE 이상이고 TTL은 15~30분이다.

불편의 실체는 **id를 다시 타이핑해야 한다**는 것이다. 봇이 `A-882`를 보냈고, Thomas는
그 메시지를 보고 있고, 그런데 `/approve A-882`를 손으로 쳐야 한다. 휴대폰에서.

**불편이 작다는 점을 먼저 인정하는 것이 이 문서의 출발점이다.** 승인은 하루 몇 건이고,
매 건이 "다시 묻지 않기로 한 것"이 아니라 "이번에 하기로 한 것"이다.

## 3. 옵션

### 옵션 A — 아무것도 하지 않는다

- **비용:** 0. **잃는 것:** 타이핑 몇 초 × 하루 몇 건.

### 옵션 B — 프론트데스크가 명령을 **제안**한다 ★ 권고

Thomas가 "승인"이라고 말하면, 프론트데스크가 **승인하지 않고** 정확한 명령을 되돌려준다:

```
Thomas: 승인
Agent:  대기 중인 승인이 하나 있습니다.
        메모리 후보 M-31을 VALIDATED로 승격 (만료 12분 남음)
        승인하시려면: /approve A-882
```

**이 저장소는 이미 이 모양을 한 번 만들었다.** `CANCEL_TASK` 턴은 취소하지 않는다 —
엔트리를 read-only로 찾아서 `/cancel <id>`를 제안한다(`frontdesk._propose_cancel`). 이유가
그대로 여기에도 적용된다: 모델이 "그거"를 잘못 해석할 수 있고, 세션 프롬프트에는 외부에서
온 텍스트가 섞일 수 있다. **모델이 제안하고, 운영자의 명시적 명령이 처분한다.**

- **거버넌스 변경: 없음.** `explicit_approval_expression_required`도,
  `invalid_approval_sources`도, 역할 계약도 그대로다. 여전히 승인하는 것은 `/approve <id>`다.
- **프론트데스크 권한 확대: 없음.** 승인 목록 읽기는 `INTERNAL_READ`이고, 발급/소비는
  여전히 코드 경로가 없다. `unsupported_capabilities`는 손대지 않는다.
- **비용:** 낮음. `frontdesk_turn`에 read-only 턴 1개(`QUERY_APPROVALS` 성격) 추가 +
  `approval` 콘솔의 기존 렌더러 재사용. `_propose_cancel`과 같은 구조.
- **얻는 것:** id를 **읽어서 옮겨 적는** 일이 사라진다. 남는 것은 탭 한 번.
- **남는 불편:** 여전히 두 메시지다.

### 옵션 C — 비금융·단일 대기 건에 한해 자연어 승인 허용

`invalid_approval_sources`에서 `ambiguous_expression`의 적용 범위를 좁히고,
`explicit_approval_expression_required`에 예외 조건을 단다.

- **조건 (제안):** 대기 중 승인이 **정확히 1건** + 비금융 + fingerprint 불변 + 미만료.
- **비용:** 정책 버전업 + 검증기 + 드리프트 게이트. 그리고 프론트데스크에
  `APPROVAL_RESPONSE` 턴이 생기므로 **역할 계약의 `unsupported_capabilities` 축소**가
  뒤따른다 — 이 문서가 다루는 것 중 유일하게 권한 경계를 실제로 옮기는 안이다.
- **위험 (정직하게):**
  - "정확히 1건"은 **경주 조건**이다. 스케줄러가 그 사이에 두 번째 승인을 만들면 Thomas가
    "승인"이라고 친 대상이 그가 본 것과 다를 수 있다.
  - 세션 프롬프트에는 검색 결과·작업 결과가 섞여 들어간다. 자연어 승인은 그 텍스트에
    **조종 가능한 표면**을 하나 만든다. 지금은 그런 표면이 없다.
  - `emoji_only`가 무효 출처에 있는 이유와 같은 계열의 위험이다.
- **얻는 것:** 한 메시지로 끝난다.

### 옵션 D — Standing grant (한 번 승인 → 경계 안에서 재사용)

이미 제안서가 있다: `CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md` Part ③ / 결정 D4
(`approval.v0.3`, `eligible_scopes: [WORKSPACE_REVERSIBLE_WRITE]`, TTL ≤ 7일, `max_uses` 상한).

- **이 문서의 범위 밖이다.** D는 "묻는 **횟수**"를 줄이고, B/C는 "**대답하는 방법**"을
  바꾼다. 서로 독립이며, D는 그쪽 문서의 D4 결정으로 남는다.
- 다만 **순서에 대한 의견 하나**: D가 먼저 오면 승인 왕복 자체가 줄어들어 B/C의 가치가
  작아진다. C를 검토하기 전에 D를 먼저 보는 것이 합리적이다.

---

## 4. 옵션 비교

| | A | B | C | D |
|---|---|---|---|---|
| 정책 변경 | 없음 | **없음** | 필요 | 필요 |
| 역할 계약 확대 | 없음 | **없음** | 필요 | 없음 |
| 승인 1건당 메시지 | 1 (id 타이핑) | 2 (탭) | **1** | 0 (경계 안) |
| 프롬프트 주입 표면 | 없음 | 없음 | **생김** | 없음 |
| 잘못 승인될 수 있는가 | 아니오 | 아니오 | 경주 조건 하에 가능 | 경계 안에서 |

---

## 5. 권고

**B를 하고, C는 하지 않는다. D는 별도 문서의 결정으로 남긴다.**

근거:

1. **B는 불편의 대부분을 없애면서 정책도 권한 경계도 건드리지 않는다.** 실제 불편은
   "승인 여부를 결정하는 일"이 아니라 "id를 옮겨 적는 일"이었고, B는 정확히 그것만 없앤다.
2. **C가 사는 것은 메시지 하나다.** 그 대가로 정책 예외 조항, 경주 조건, 그리고 지금은
   존재하지 않는 프롬프트 주입 표면이 생긴다. 승인이 존재하는 이유에 비해 교환비가 나쁘다.
3. **금융은 어느 옵션에서도 제외다.** 오늘 금융 승인은 채널에 존재하지도 않고
   (주문 경로는 운영자 전용 canary 스크립트뿐), 생기더라도 `/approve <id>`만이어야 한다.
4. B를 하고도 불편이 남으면 그때 C를 재검토하면 된다. **B는 C로 가는 길을 막지 않는다** —
   반대로 C를 먼저 하면 B는 의미가 없어진다.

---

## 6. 요구되는 Thomas 결정

| # | 결정 | 성격 |
|---|---|---|
| V1 | 옵션 B(승인 명령 제안 턴) 구현 착수 | 거버넌스 변경 없음 — 구현 승인만 |
| V2 | 옵션 C(비금융 자연어 승인)를 검토할 것인가 | **정책 변경 + 역할 계약 축소** |
| V3 | (V2가 "예"일 때) `ambiguous_expression` 예외의 정확한 경계 | 정책 조문 |
| V4 | Standing grant(D4)를 지금 볼 것인가 | 별도 문서의 결정 |

권장: **V1만 지금** → 운용해보고, 남는 불편이 실재하면 V4 → 그래도 남으면 V2.

**어느 결정에서도 유지되어야 하는 것:** 금융 실행의 승인은 명시적 `approval_id`,
단발 사용, 실행 직전 재검증. 자연어 단독으로 주문이 나가는 경로는 만들지 않는다.

---

*작성 2026-07-28. 근거: `governance/GOVERNANCE_POLICY.yaml`
(`control_channel`, `approval_lifetime`), `03_ROLE_CONTRACTS/CONVERSATION_FRONTDESK_ROLE.md`
(`unsupported_capabilities`), `runtime/mvp_runtime/frontdesk.py` (`_propose_cancel` 선례),
`runtime/mvp_runtime/approval.py`, `docs/proposals/CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md`
Part ③.*
