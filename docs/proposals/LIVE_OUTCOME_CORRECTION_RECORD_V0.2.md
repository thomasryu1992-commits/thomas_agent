# 제안: 라이브 아웃컴 정정 레코드 — 사람은 판단을 넣고, 산술은 런타임이 한다 (DRAFT v0.2)

**상태:** DRAFT — 설계 선행(구현 아님). 코드 변경 없음. **v0.1을 대체한다.**
**v0.1에서 바뀐 것:** §5-1이 남긴 반론 — *"SUPERSEDE는 런타임이 기록하지 않은, 사람이 계산한
숫자를 믿게 된다"* — 이 **이 결함에는 성립하지 않는다**는 것이 원장 확인으로 드러났다. 정정값은
대상 행 자신의 산술이다. 그래서 v0.2는 **fail-closed 규칙 5번**(런타임 재계산)과 그 규칙을 걸 수
있는 정정과 없는 정정을 가르는 **`basis` 필드**를 추가한다. 나머지 설계는 v0.1 그대로다.
**작성 배경:** 2026-08-23. `REMAINING_WORK.md` §C.

---

## 0. 한 줄

`live_out_e56cf310dcc07d9c6edd` 한 행이 `+398.03R`을 기록하는데 참값은 `-0.887R`이다. 원장은
append-only이고 그 행은 **자기 해시를 통과한다**(손상이 해시 이전에 일어났다). 그래서 정정은
편집이 아니라 **새 레코드 타입**이어야 한다.

## 1. 왜 기존 도구로 안 되는가 (기각된 대안 넷)

v0.1 §1과 동일하며 요약만 남긴다. 넷 다 시도됐고 넷 다 표현하지 못한다.

| 대안 | 왜 안 되는가 |
|---|---|
| 값을 손으로 수정 | 재계산된 해시를 **통과한다**. 돈 숫자를 누가 바꿨다는 기록이 남지 않는다 |
| 같은 `outcome_id`로 정정행 append | `LIVE_HISTORY_DUPLICATE`로 라이브 이력 읽기가 **전부** fail-closed |
| 같은 `settlement_id`로 append | 동일하게 거부 |
| `drawdown_excluded_strategy_ids` | 의미가 다르고, 같은 배치 17행을 함께 날리며, **결과가 더 틀리다**(-2.9901R 대 참값 -0.8871R) |

## 2. 설계

### 2-1. 별도 파일, 별도 타입 (v0.1과 동일)

정정은 `live_outcomes.jsonl`에 들어가지 않고 새 파일 `live_outcome_corrections.jsonl`에 쌓인다.
아웃컴 파일에 무엇을 넣든 `read_live_outcomes`의 중복·해시 검사를 통과해야 하는데, 정정은
정의상 **기존 행을 가리키는** 레코드라 그 검사와 구조적으로 충돌한다. 파일을 나누면 아웃컴
읽기 경로는 한 글자도 바뀌지 않는다.

### 2-2. 레코드 모양 — `basis`가 추가됐다

```json
{
  "schema_version": "live_outcome_correction.v0.2",
  "correction_id": "live_corr_<short_id>",
  "corrects_outcome_id": "live_out_e56cf310dcc07d9c6edd",
  "corrects_record_sha256": "sha256:4f60...",
  "disposition": "SUPERSEDE",
  "basis": "DERIVED",
  "reason_code": "OUTCOME_QUANTITY_MISMATCH",
  "reason": "<사람이 읽는 한 문단>",
  "corrected_realized_pnl_usdt": -0.1728,
  "corrected_result_R": -0.8871,
  "evidence": { "venue_filled_qty": 0.002, "book_qty": 0.001,
                "exit_price": 77708.5, "entry_price": 77881.3 },
  "approval_id": "approval_<...>",
  "corrected_by": "thomas",
  "created_at_utc": "...",
  "record_sha256": "sha256:..."
}
```

닫힌 스키마(`additionalProperties: false`). `corrects_record_sha256`이 **핵심 필드**다: 정정은
자기가 무효화하는 행의 해시를 지목하므로, 원본이 다른 것으로 바뀌면 정정이 그 행을 더 이상
가리키지 않고 fail-closed된다. 정정이 엉뚱한 행에 붙는 사고가 구조적으로 불가능해진다.

### 2-3. `disposition` 둘 (v0.1과 동일)

- **`VOID`** — 어떤 계산에도 들어가지 않는다. `corrected_*` 없음.
- **`SUPERSEDE`** — 이 행 대신 `corrected_*`를 쓴다.

`live_out_e56cf310dcc07d9c6edd`는 **SUPERSEDE**여야 한다. VOID로 지우면 실제 일어난 0.887R
손실까지 사라져 드로다운이 여전히 틀린다 — 방향만 반대로. `drawdown_excluded_strategy_ids`가
-2.9901R로 참값에서 **더 멀어진** 측정이 정확히 이 함정이고, 그것이 SUPERSEDE를 고르는 근거다.

### 2-4. **`basis` — 이 정정의 숫자는 어디서 왔는가 (v0.2 신규)**

v0.1은 SUPERSEDE의 대가를 "사람이 넣은 수를 런타임이 믿는다"로 적었다. **이 결함에는 그 대가가
없다.** 원장의 그 행이 필요한 값을 전부 자기가 갖고 있다:

```
quantity: 0.001   entry_price: 77881.3   exit_price: 77708.5   risk_usdt: 0.1948
```

```
realized = (77708.5 − 77881.3) × 0.001 = −0.1728
R        = −0.1728 ÷ 0.1948            = −0.8871
```

둘 다 §2-2의 `corrected_*`와 일치한다. 즉 정정값은 사람의 숫자가 아니라 **그 행 자신의
산술**이고, 이 산술은 `#753`의 `_pnl_agrees_with_prices`가 이미 쓰는 바로 그 식이다. 기록된
`+398.03R`은 청산측만 거래소 수량(0.002)으로 값을 매겨 나온 값이며, `evidence.venue_filled_qty`는
**참값을 계산하는 데 쓰이지 않는다** — 기록된 값이 왜 틀렸는지를 설명할 뿐이다.

그래서 `basis` 둘:

- **`DERIVED`** — `corrected_*`가 대상 행의 필드만으로 재계산된다. 런타임이 직접 계산해서
  대조한다(§2-6 규칙 5). **사람은 숫자를 넣지 않고 판단만 넣는다.**
- **`ATTESTED`** — 재계산이 불가능하다. 행의 가격 자체가 틀린 경우가 이쪽이다. `corrected_*`의
  출처가 외부이므로 승인 해시 말고는 아무것도 그 값을 보증하지 않는다.

**이 구분을 스키마가 갖는 이유**는 규칙 5를 걸 수 있는 정정과 없는 정정이 실제로 다르기
때문이다. `basis` 없이 규칙 5를 전역으로 걸면 ATTESTED 정정이 영원히 거부되고, 규칙 5를 아예
안 걸면 DERIVED 정정이 검증 없이 통과한다. 한 필드가 두 정책을 가르는 것이 아니라, **두
population이 원래 다르다**는 사실을 이름 붙인 것이다.

`live_out_e56cf310dcc07d9c6edd`는 **`DERIVED`**다.

### 2-5. 적용 지점 — 소비자 5곳이 아니라 한 곳 (v0.1과 동일)

`read_live_outcomes`는 모든 소비자가 지나는 **단일 병목**이다(`breaker_watch`·`cycle`·
`live_pnl` 내부 3곳·`live_promotion`·`run_slippage_probe`). 정정은 그 함수 안에서 한 번
적용되고, 소비자는 하나도 바뀌지 않는다.

```
read_live_outcomes(root)
  1. 기존 검증(해시·중복) 그대로
  2. read_live_outcome_corrections(root)   # 없으면 빈 리스트
  3. corrects_outcome_id + corrects_record_sha256으로 매칭
  4. basis == DERIVED 이면 corrected_* 를 재계산해 대조 (규칙 5)
  5. VOID → 행 제외 / SUPERSEDE → corrected_* 를 덮은 사본 반환
```

**원본 행은 파일에서 사라지지 않는다.** 읽기가 정정된 뷰를 돌려줄 뿐이고, 원장은 "무슨 일이
있었는지"와 "누가 언제 왜 정정했는지"를 둘 다 보관한다.

### 2-6. Fail-closed 규칙 — 넷에서 **다섯**으로

이 기능의 위험은 정정이 **거짓말할 수 있다**는 것이므로, 의심스러우면 전부 BLOCK이다.

1. 정정이 존재하지 않는 `outcome_id`를 가리킴 → `CORRECTION_TARGET_MISSING`
2. `corrects_record_sha256`이 실제 행의 해시와 불일치 → `CORRECTION_TARGET_CHANGED`
3. 한 `outcome_id`에 정정이 둘 이상 → `CORRECTION_AMBIGUOUS`
4. `approval_id`가 없거나 승인 기록과 내용 해시 불일치 → `CORRECTION_UNAPPROVED`
5. **(신규)** `basis == DERIVED`인데 `corrected_*`가 대상 행에서 재계산한 값과 불일치 →
   `CORRECTION_ARITHMETIC_DISAGREES`

다섯 다 `read_live_outcomes`에서 raise한다. 즉 **잘못된 정정은 라이브 이력을 읽히지 않게
만든다** — 정정을 조용히 무시하는 것보다 낫다. 조용히 무시하면 브레이커가 정정됐다고 믿는
숫자와 실제로 쓰는 숫자가 갈린다.

**규칙 5의 허용오차는 `_pnl_agrees_with_prices`의 것을 재사용한다**(`max(notional × 1e-6, 1e-6)`).
새 상수를 두면 같은 산술을 재는 두 숫자가 갈라진다 — `#753`이 그 허용오차를 정한 근거(28행 중
27행이 float noise 2e-16 이내, 틀린 1행은 99.8% 빗나감)가 여기 그대로 유효하다.

## 3. 거버넌스 (v0.1과 동일)

정정은 돈 숫자를 바꾸므로 승격과 같은 등급이다: **`--request` → Thomas `/approve` →
`--confirm`.** 승인 내용 해시에 `corrects_outcome_id`·`disposition`·`basis`·`corrected_*`가
들어가므로 승인된 것과 다른 정정은 적용될 수 없다. `scripts/correct_live_outcome.py`가 도어이고
승격 스크립트와 같은 모양(`--list` / `--request` / `--confirm`)을 따른다.

**Claude는 이 스크립트를 실행하지 않는다** — 라이브 머니 경로.

## 4. 비용과, 이 제안이 사지 않는 것 (v0.1과 동일)

**사는 것:** 드로다운 브레이커의 0.887R 오염 제거. 그리고 재발 시 절차.

**사지 않는 것:** 이 행은 **드로다운에만** 영향을 준다. 일간·주간 브레이커는
2026-08-24T00:00Z 롤오버로 자연 이탈했고, 연속손실은 `PROBE-`를 건너뛰며, 라이프사이클·승격·
`live_allowance`·대시보드는 페이퍼를 읽는다. **10R 한도 대비 0.887R**이 회수하는 전부다.

**그래서 급한 일이 아니다.** 급하지 않은 것을 급한 것처럼 만들지 않기 위해 여기 적는다.
다만 시간이 해결하지도 않는다 — 오염은 영구적이고, **다음 라이브 무장 전에는 정리돼 있어야
한다**.

## 5. 열린 질문 (Thomas 결정)

**1. `SUPERSEDE`를 쓰는가, `VOID`만 두는가.**
v0.1이 남긴 반론은 §2-4가 해소한다 — 이 행의 정정값은 사람의 숫자가 아니라 행 자신의
산술이고, 규칙 5가 그것을 강제한다. **남는 결정은 좁아졌다: `ATTESTED`를 지금 도입할 것인가,
아니면 `DERIVED`만 구현하고 재계산 불가능한 정정이 실제로 필요해질 때까지 미룰 것인가.**
후자를 권한다 — 지금 필요한 정정은 하나이고 그것은 DERIVED다. `ATTESTED`는 승인 해시 외에
아무 보증이 없는 경로이므로, 쓸 곳이 생기기 전에 만들면 검증 없는 문만 열어두는 셈이다.

**2. 정정 파일도 `record_sha256` **체인**을 갖는가, 행별 자체 해시만인가.**
아웃컴 파일은 후자다. 체인은 삭제·순서변경을 잡고 자체 해시는 못 잡는다.
**정정은 "없어지면 돈 숫자가 바뀌는 유일한 레코드"다** — 한 줄이 지워지면 런타임은 조용히
원래의 +398R로 돌아가고 아무것도 울리지 않으며, 그 되돌아감이 정상 동작과 구별되지 않는다.
레코드 수가 적어 체인 비용은 낮다. **체인을 권한다.**
반론도 적어둔다: 한 서브시스템에 해싱 방식이 둘이 되는 것 자체가 드리프트 위험이고,
"하나의 불변식은 한 곳에서"라는 이 레포의 원칙과 부딪친다. 셋 중 판단이 가장 갈릴 항목이다.

**3. `live_outcomes.jsonl`의 원본 행에 표식을 남기는가.**
**남기지 않는 쪽을 권한다.** 남기려면 append-only를 깨야 하고, 그것이 이 제안이 존재하는
이유다. 셋 중 논쟁의 여지가 가장 적다.
