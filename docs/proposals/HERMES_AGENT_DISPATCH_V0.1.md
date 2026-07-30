# 제안: Hermes 에이전트 구동 문 — 어시스턴트가 무엇을 *시작*시킬 수 있는가 (DRAFT v0.1)

**상태:** DRAFT — 설계 선행(구현 아님). **§6 결정 확정**(모드 A, 캡 없음, $50 USD/일 알림,
allowlist, P3, 거버넌스 위치) + **§7 정책 diff 초안**. 남은 것: §12–16 정식 승인(PR #373).
**근거:** 아키텍처 §12–16, `governance/GOVERNANCE_POLICY.yaml`(authority P0–P6, control_channel),
`runtime/mvp_runtime/halt_bridge.py`·`read_bridge.py` 선례.
**선행 관계:** 관측 문(read_bridge)은 완료·라이브. 이 문서는 그 다음 단계인 "구동"을 다룬다.

---

## 0. 한 줄

관측은 끝났다. 다음 요구는 "Hermes가 에이전트를 **구동**한다"인데, 이것은 halt 문의 핵심
원리 — *어시스턴트는 멈출 수는 있어도 시작할 수는 없다* — 를 정면으로 뒤집는다. 이 문서의
결론: 그 원리를 깨지 않고 "시작"을 허용하는 유일한 길은 **효과 등급(effect class)으로 자르는
것**이며, 그 절단선은 `P3 상한 + 롤 allowlist`다.

## 1. 문제 — 왜 "구동"은 "관측"과 다른가

- **관측 문이 안전한 이유:** 아무것도 mutate하지 않는다. `_READS` 테이블에 mutating verb가
  구조적으로 부재하고, 테스트가 그 부재를 지킨다.
- **halt 문이 안전한 이유:** 두 verb(kill/pause)가 모두 fail-safe(멈춤)다. `resume`은 구조적
  거부다 — 어시스턴트는 웹·검색 등 **untrusted text**를 읽어 prompt injection의 착지점이 되므로,
  실주문을 재개하는 경로를 그쪽에 두지 않는다.
- **"구동(drive)"은 이 원리와 충돌한다:** 일을 *시작*시키는 것 = halt가 금지하는 바로 그것.

**그러나 측정된 사실이 문제의 모양을 바꾼다.** 지금 라우터블 롤은
`general.specialist`(P3), `validation.independent`(P2), `research.general`,
`translation.general`, `content.general` — **전부 REVIEW_ONLY, P≤3, 머니 패스 없음.**
트레이딩은 이들과 무관한 별도 스케줄러(별 컨테이너)다. 즉 "에이전트를 시작"한다고 다 위험한
것이 아니다. 위험한 것은 **무엇을 시작하느냐**다.

## 2. 무엇을 포기하지 않아야 하는가 (불변식)

1. **어시스턴트는 untrusted 측이다.** injection이 이 문을 통과하더라도 **외부 행동(P5)·머니
   패스에 절대 닿지 못한다.** (halt의 `resume` 거부와 동일 등급의 보장.)
2. **권한은 확장 불가.** `approval_cannot_expand_authority: true`,
   `tool_or_program_may_expand_authority: false`. 이 문은 어떤 ceiling도 올려선 안 된다.
3. **fail-closed.** 미허용 템플릿/롤/예산초과 → BLOCK, 추측 없음. 전형적 `reason_code`.
4. **귀속.** 모든 dispatch는 actor `assistant_bridge`로 원장에 기록되어 operator 발
   dispatch와 구분된다.
5. **문은 키를 갖지 않는다.** provider/venue 키, `MVP_LIVE_*` 없음 — halt/read 문 선례 그대로.
6. **§12–16 준수.** 이 능력 추가 자체가 Expansion 결정이다 — Thomas 승인 + 버전드 거버넌스
   업데이트 + 감사.

## 3. 핵심 통찰 — 축은 "관측 vs 구동"이 아니라 "효과 등급"

halt가 안전하고 resume이 금지되는 진짜 이유는 **효과**다. "구동"은 하나의 덩어리가 아니라
스펙트럼이다:

| 효과 등급 | 예 | 판정 |
|---|---|---|
| **fail-safe 구동** | analysis·research·translation·content 태스크 dispatch → 기존 파이프라인(Prime 계획 → 라우팅 → gated 모델 호출 → 독립 검증 → 감사 → 메모리). 산출물은 *검토된* 리포트. P≤3, 외부 행동 없음, REVIEW_ONLY. | **읽기만큼 fail-safe** |
| **금지 구동** | 외부 행동(P5)·머니 패스를 시작/재개 | **resume과 동급 — 구조적 거부** |

→ 그래서 이 문은 "무엇이든 시작"이 아니라 "**P3 상한 + 롤 allowlist 안에서만 시작**"이다.
dispatch가 파이프라인을 타도 권한은 그대로다 — 누가 시작했든 태스크는 같은 게이트를 통과하고,
dispatch 행위 자체는 어떤 ceiling도 올리지 않는다.

## 4. 메커니즘 (halt/read 문 패턴 재사용)

세 번째 어시스턴트 문 `dispatch.sock`, **자체 컨테이너**(별 authority = 별 문, halt/read 선례).

- **요청:** allowlist된 템플릿/롤로 태스크 **1건** 제출, `reason` 필수, actor=`assistant_bridge`.
- **구조적 상한** (halt의 `_ALLOWED`처럼 정책 상수에서 빌드 — config·env·prompt로 못 넓힘):
  1. **P3 ceiling** — assistant-dispatched 태스크는 `assignment_granted ≤ P3`. P5/P6 도달 불가.
     정책 invariant이 강제(코드의 선의가 아니라).
  2. **롤 allowlist** — `general`/`research`/`translation`/`content`.specialist만. crypto/trading
     롤은 목록에 **없다**(스케줄러의 stored-template 선례 — 임의 실행 아님).
  3. **사용량 계측 (캡 없음 — Thomas 결정 2026-07-30)** — dispatch 사용량을 집계하되
     fail-closed 상한·레이트 캡은 **두지 않는다**. `execution_budget` 계측만 재사용.
  4. **감사** — 매 dispatch가 원장 이벤트.
  5. **키 없음** — 문 컨테이너에 머니 키 부재.
  6. **임계 알림만 (자동 정지 없음 — Thomas 결정)** — 어시스턴트-발 dispatch 사용량이 임계치를
     넘으면 operator에 **알림**을 보낸다. 그게 전부다 — 문은 스스로 멈추지 않는다. 대응(halt
     문으로 수동 정지)은 사람의 몫. 어시스턴트-발 dispatch 목록은 read 문/대시보드로 노출.

## 5. 유일하게 큰 결정 — 자율 시작 vs operator 확인

| | 옵션 A: 예산 내 자율 | 옵션 B: operator 확인 ★권고 |
|---|---|---|
| 흐름 | 어시스턴트가 직접 dispatch | 어시스턴트는 **제안**만; Thomas가 인증 텔레그램 채널에서 확인해야 실행 |
| 안전 근거 | 상한(P3+allowlist+budget)이 전부 | 상한 + **사람 게이트** |
| injection 최악 | fail-safe 작업을 시작시킴(비용·소음, 위험 아님) | 사람 없이는 fail-safe 작업조차 시작 못 함 |
| 재사용 | 새 예산 로직 | 기존 approval 기구(approval_id/fingerprint, one_time_use, 30분 TTL) 그대로 |
| 정책 정합 | — | `new_high_risk_approval_creation_allowed: false`의 정신과 일치 |

> **Thomas 결정 (2026-07-30): 옵션 A — 예산 내 자율.** 문서 원 권고는 B였으나 Thomas가 A를
> 선택. 기록으로 남긴다: 사유는 대화 흐름의 즉시성(제안→확인 왕복이 대화형 프런트의 목적을
>해침)으로 추정되며, 대상 롤이 전부 REVIEW_ONLY·P≤3·머니 패스 없음이라 injection 최악도
> "낭비된 모델 예산", 위험이 아니다.
>
> **남는 안전장치 (Thomas 결정 2026-07-30):** 예방 게이트(사람 확인)와 예방 캡(fail-closed
> 예산·레이트)을 **둘 다 뺐다.** 남는 것은 —
> (a) **P3 ceiling·롤 allowlist** — 정책 상수에서 빌드, trading 롤 부재를 테스트로 고정
>     (**유지 필수**: 위험/머니 패스를 막는 구조적 벽은 이것뿐),
> (b) **사용량 임계 알림만** (§4.6) — 자동 정지 없음, 사람이 알림 보고 halt로 수동 대응.
>
> **명시적으로 기록하는 안전 완화 (relaxation, recorded as one):** 캡과 서킷브레이커를 뺐으므로
> **runaway는 자동으로 멈추지 않는다.** injection이 문을 통과하면, 사람이 알림을 보고 halt를
> 걸 때까지 fail-safe 태스크를 계속 dispatch한다. 태스크 자체는 P≤3·머니 패스 없음이라 *위험*은
> 아니지만, **비용은 실재한다** — dispatch된 태스크는 런타임의 provider chain(openrouter 우선,
> Hermes와 **공유 키**)으로 실제 모델 호출을 태우므로, storm이 무료 티어 일일 쿼터를 소진해
> 런타임의 정당한 analysis/validation까지 함께 저하시킬 수 있다(이미 기록된 shared-key 실패
> 모드). **알림이 유일한 트리거**다 — 그래서 임계치를 어디에 두느냐가 이 완화의 전부다.
>
> **승격 경로:** 특정 템플릿이 반복 안전으로 증명되면(§12) 이후 자동화 확대 — 감사된 결정.

## 6. Thomas 결정 — 확정 현황 (2026-07-30)

1. **dispatch 문 생성:** 방향 확정(①→② 진행). **정식 §12–16 Expansion 승인 + 버전드 거버넌스
   + 감사**는 PR #373 리뷰/병합으로 별도 완결.
2. **시작 모드:** ✅ **A (예산 내 자율).** §5의 남는 안전장치(구조적 벽 + 임계 알림)를 동반.
3. **롤 allowlist:** ✅ `general.specialist`·`research.general`·`translation.general`·
   `content.general`. **crypto/trading 제외**(부재를 테스트로 고정).
4. **P3 ceiling:** ✅ 예. 사람 게이트가 없으니 P5 도달 불가가 머니 패스를 막는 유일 불변식.
5. ~~예산/레이트 상한~~ — **결정됨 (Thomas 2026-07-30): 하드 캡 없음, 통화 기준 임계 알림만.**
   어시스턴트-발 dispatch 누적 비용이 **하루 50 USD**를 넘으면 operator 알림
   (`execution_budget` 누적 비용, `cost_currency=USD`). 자동 정지 없음.
   **기록된 커버리지 caveat:** 현재 provider는 무료 티어(`…:free`, 비용 ≈ 0)라 이 통화 임계는
   사실상 **휴면**이다 — dispatch storm이 나도 달러 비용이 안 올라 알림이 안 뜬다. 즉 유료
   모델/실비용이 들어오기 전까지 **실질 능동 통제는 §4의 구조적 벽(P3+allowlist)뿐**이고, 이
   $50/일은 *비용이 실재하게 되는 날*을 위한 잠복 머니가드다. 무료 티어의 실제 제약인
   요청-건수 storm(공유 쿼터 ~200/일)을 잡으려면 건수 기준 알림을 병행해야 하지만, Thomas는
   통화 단일 기준을 선택 — 이 커버리지 공백은 알고 남긴 것이다.
6. **거버넌스 위치:** ✅ `control_channel.assistant_dispatch` 블록 + `authority`의
   `assistant_dispatch_gate`(`thomas.dispatch.assistant_gate`). 게이트 조건: P3 ceiling,
   allowlist 소속, 머니 패스 없음, reason 기록, post-dispatch 감사. `policy_version`
   1.2.0 → 1.3.0 (승인 시). 초안 → §7.

## 7. 제안 거버넌스 정책 diff (초안 — `GOVERNANCE_POLICY.yaml`에 아직 **미적용**)

> 아래는 **제안**이다. 승인 전까지 `governance/GOVERNANCE_POLICY.yaml`(활성 정책 원본)을
> 건드리지 않는다 — 활성 정책 수정 자체가 §12–16 승인 사안이다. 승인 시 두 블록을 추가하고
> `policy_version`을 `1.2.0` → `1.3.0`으로 올린다. 조건 이름은 §4의 구조적 상한에 매핑되며,
> P5 게이트의 조건들이 "코드로 강제된다"고 명시하는 것과 같은 방식으로 코드에서 강제된다.

**`authority:` 아래 추가 — P5 게이트와 동형:**
```yaml
  # PROPOSED — Hermes 구동 문 (DRAFT v0.1, 미활성). 어시스턴트가 시작하는 모든 dispatch가
  # 통과할 게이트. 정의한다고 열리지 않는다 — 어시스턴트 dispatch를 암시가 아니라 정책
  # 게이트에 대해 감사 가능하게 만들 뿐. gate_grants_authority: false — dispatch된 태스크는
  # 자신의 P<=3 ceiling으로 실행되고, 이 게이트가 그 ceiling을 올리지 않는다.
  assistant_dispatch_gate:
    gate_id: thomas.dispatch.assistant_gate
    applies_to_actor: assistant_bridge
    requires:
      - effective_permission_ceiling_p3   # dispatch된 태스크는 P3 상한 — P5/P6 도달 불가
      - role_in_dispatch_allowlist        # allowlist 소속 롤만
      - no_money_path_capability          # trading/venue/live-order 롤·툴 없음
      - reason_recorded                   # 모든 dispatch는 사유를 명시·기록
      - post_dispatch_audit               # actor=assistant_bridge, dispatch당 원장 1건
    gate_grants_authority: false
```

**`control_channel:` 아래 추가 — operator 콘솔과 병렬:**
```yaml
  # PROPOSED — Hermes 구동 문 (DRAFT v0.1, 미활성). 어시스턴트의 dispatch 레인. operator
  # 콘솔(=Thomas 인증)과도, halt 문(=fail-safe 정지)과도 다르다: 이 레인은 일을 *시작*시키므로
  # 신뢰가 아니라 효과 등급으로 제한된다.
  assistant_dispatch:
    actor: assistant_bridge
    mode: AUTONOMOUS_WITHIN_ALLOWLIST     # Thomas 결정 2026-07-30 (옵션 A); dispatch별 operator 확인 없음
    permission_ceiling: P3
    role_allowlist:
      - general.specialist
      - research.general
      - translation.general
      - content.general
    trading_role_absent: structural_and_test_locked   # crypto/trading 롤은 목록에 구조적으로 부재
    hard_rate_cap: none                   # Thomas 결정 2026-07-30: fail-closed 캡 없음
    self_halt_on_spike: false             # Thomas 결정: 알림만, 서킷브레이커 없음
    spend_alert:
      meter: execution_budget_cumulative_cost
      threshold_amount: 50
      threshold_currency: USD             # 3-letter; provider가 무료 티어인 동안 휴면(기록된 caveat)
      period: per_day
      action: notify_operator             # 자동 정지 없음 — halt 문으로 사람이 정지
    new_high_risk_approval_creation_allowed: false
```

---

## 부록 — 이 설계가 지키는 것들 (자기 점검)

- **재사용 우선:** intake 파이프라인, approval 기구, `execution_budget`, 문(door) 패턴 —
  새 authority를 발명하지 않는다.
- **fail-closed:** 미허용/불확실 → BLOCK.
- **halt 비대칭 보존:** 어시스턴트는 여전히 외부 행동/머니 패스에 닿지 못한다 — "시작"을
  허용해도 그 절단선은 P3에서 그대로다.
- **귀속·감사:** operator 발과 어시스턴트 발이 원장에서 구분된다.
