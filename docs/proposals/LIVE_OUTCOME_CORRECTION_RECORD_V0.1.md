# 제안: 라이브 아웃컴 정정 레코드 — 원장을 고치지 않고 틀린 행을 무효화한다 (DRAFT v0.1)

**상태:** DRAFT — 설계 선행(구현 아님). 코드 변경 없음. 라이브 원장의 스키마를 늘리는
제안이므로 구현은 Thomas 승인 후 별도 증분.
**작성 배경:** 2026-08-23. `REMAINING_WORK.md` §C가 *"There is no correction mechanism, and
building one is the open item"* 으로 남긴 항목. #752가 재발을 막았고 #753이 증거 보드의
보증을 끊었지만, **둘 다 그 행을 그 자리에 남긴다.**
**근거:** `live_pnl.read_live_outcomes`(단일 검증 읽기), `live_position.build_live_outcome_record`
(`outcome_id` 파생), §C "One live outcome row is wrong and there is no way to correct it".

---

## 0. 한 줄

`live_out_e56cf310dcc07d9c6edd` 한 행이 `+398.03R`을 기록하는데 참값은 `-0.887R`이다. 원장은
append-only이고 그 행은 **자기 해시를 통과한다**(손상이 해시 이전에 일어났다). 그래서 정정은
편집이 아니라 **새 레코드 타입**이어야 하고, 이 문서는 그 모양을 정한다.

## 1. 왜 기존 도구로 안 되는가 (기각된 대안 넷)

CLAUDE.md의 **Reuse first**는 "기존 소유자가 정말로 표현할 수 없을 때만" 새 스키마를 허용한다.
넷 다 시도됐고 넷 다 표현하지 못한다 — 이것이 이 제안의 전제다.

| 대안 | 왜 안 되는가 |
|---|---|
| 값을 손으로 수정 | 해시를 재계산하면 **통과한다**. 아무도 조용히 못 바꿔야 하는 그 원장에, 누군가 돈 숫자를 바꿨다는 기록이 남지 않는다. 이것이 지름길이 아니라 함정인 이유 |
| 같은 `outcome_id`로 정정행 append | `read_live_outcomes`가 `LIVE_HISTORY_DUPLICATE`로 raise → **브레이커·리스크가드·승격보드가 한꺼번에** fail-closed. 라이브 이력 전체가 읽히지 않는다 |
| 같은 `settlement_id`로 append | 동일하게 거부 |
| `drawdown_excluded_strategy_ids` | 의미가 다르고(나쁜 숫자 ≠ 은퇴 전략), 같은 배치 17행을 함께 날리며, **결과가 더 틀리다** — 제외 시 -2.9901R, 참값 -0.8871R |

## 2. 설계 — `live_outcome_correction`

### 2-1. 별도 파일, 별도 타입

정정은 `live_outcomes.jsonl`에 **들어가지 않는다.** 새 파일
`live_outcome_corrections.jsonl`에 자체 스키마로 쌓인다. 이유는 하나뿐이고 그것으로 충분하다:
아웃컴 파일에 무엇을 넣든 `read_live_outcomes`의 중복·해시 검사를 통과해야 하는데, 정정은
정의상 **기존 행을 가리키는** 레코드라 그 검사와 구조적으로 충돌한다. 파일을 나누면 아웃컴
읽기 경로는 한 글자도 바뀌지 않는다.

### 2-2. 레코드 모양

```json
{
  "schema_version": "live_outcome_correction.v0.1",
  "correction_id": "live_corr_<short_id>",
  "corrects_outcome_id": "live_out_e56cf310dcc07d9c6edd",
  "corrects_record_sha256": "sha256:4f60...",
  "disposition": "VOID",
  "reason_code": "OUTCOME_QUANTITY_MISMATCH",
  "reason": "<사람이 읽는 한 문단>",
  "corrected_realized_pnl_usdt": -0.1728,
  "corrected_result_R": -0.8871,
  "evidence": { "venue_filled_qty": 0.002, "book_qty": 0.001, "exit_price": 77708.5,
                "entry_price": 77881.3 },
  "approval_id": "approval_<...>",
  "corrected_by": "thomas",
  "created_at_utc": "...",
  "record_sha256": "sha256:..."
}
```

닫힌 스키마(`additionalProperties: false`). `corrects_record_sha256`이 **핵심 필드**다: 정정은
자기가 무효화하는 행의 해시를 지목하므로, 원본이 다른 것으로 바뀌면 정정이 더 이상 그 행을
가리키지 않고 **fail-closed**된다. 정정이 엉뚱한 행에 붙는 사고가 구조적으로 불가능해진다.

### 2-3. `disposition` 둘, 그리고 왜 둘인가

- **`VOID`** — 이 행은 어떤 계산에도 들어가지 않는다. `corrected_*` 필드 없음.
- **`SUPERSEDE`** — 이 행 대신 `corrected_*` 값을 쓴다.

`live_out_e56cf310dcc07d9c6edd`는 **SUPERSEDE**여야 한다. VOID로 지우면 실제로 일어난
0.887R 손실까지 사라져 드로다운이 여전히 틀린다 — 방향만 반대로. §C가 측정한
`drawdown_excluded_strategy_ids`의 실패(-2.9901R)가 정확히 이 함정이고, 그 측정이 여기서
`SUPERSEDE`를 고르는 근거다.

### 2-4. 적용 지점 — 소비자 5곳이 아니라 한 곳

`read_live_outcomes`는 **모든 소비자가 지나는 단일 병목**이다. 확인된 호출자:
`breaker_watch`, `cycle`, `live_pnl`(내부 3곳), `live_promotion`, `run_slippage_probe`.

그래서 정정은 그 함수 안에서 한 번 적용한다 — 소비자는 하나도 바뀌지 않는다.

```
read_live_outcomes(root)
  1. 기존 검증(해시·중복) 그대로
  2. read_live_outcome_corrections(root)   # 없으면 빈 리스트
  3. 각 정정을 corrects_outcome_id + corrects_record_sha256으로 매칭
  4. VOID → 행 제외 / SUPERSEDE → corrected_* 를 덮은 사본 반환
```

**원본 행은 파일에서 사라지지 않는다.** 읽기가 정정된 뷰를 돌려줄 뿐이고, 원장은 여전히
"무슨 일이 있었는지"와 "누가 언제 왜 정정했는지"를 둘 다 보관한다.

### 2-5. Fail-closed 규칙 넷

이 기능의 위험은 정정이 **거짓말할 수 있다**는 것이므로, 의심스러우면 전부 BLOCK이다.

1. 정정이 **존재하지 않는** `outcome_id`를 가리킴 → `CORRECTION_TARGET_MISSING`
2. `corrects_record_sha256`이 실제 행의 해시와 불일치 → `CORRECTION_TARGET_CHANGED`
3. 한 `outcome_id`에 정정이 **둘 이상** → `CORRECTION_AMBIGUOUS`
4. `approval_id`가 없거나 승인 기록과 내용 해시 불일치 → `CORRECTION_UNAPPROVED`

넷 다 `read_live_outcomes`에서 raise한다. 즉 **잘못된 정정은 라이브 이력을 읽히지 않게 만든다** —
정정을 조용히 무시하는 것보다 낫다. 조용히 무시하면 브레이커가 정정됐다고 믿는 숫자와 실제로
쓰는 숫자가 갈린다.

## 3. 거버넌스

정정은 돈 숫자를 바꾸므로 승격과 같은 등급이다: **`--request` → Thomas `/approve` → `--confirm`.**
승인 내용 해시에 `corrects_outcome_id`, `disposition`, `corrected_*`가 들어가므로 승인된 것과
다른 정정은 적용될 수 없다. `scripts/correct_live_outcome.py`가 도어이고, 승격 스크립트와 같은
모양(`--list` / `--request` / `--confirm`)을 따른다.

**Claude는 이 스크립트를 실행하지 않는다.** 라이브 머니 경로이고, CLAUDE.md의 규칙 그대로다.

## 4. 비용과, 이 제안이 사지 않는 것

**사는 것:** 드로다운 브레이커의 0.887R 오염 제거. 그리고 재발 시 절차 — #752가 막았지만
막지 못한 경로가 또 나오면 이번엔 원장을 고치는 논의를 처음부터 하지 않아도 된다.

**사지 않는 것:** 이 행은 **드로다운에만** 영향을 준다(§C의 소비자 표). 일간·주간 브레이커는
2026-08-24T00:00Z 롤오버로 자연 이탈했고, 연속손실은 `PROBE-`를 건너뛰며, 라이프사이클·승격·
`live_allowance`·대시보드는 페이퍼를 읽는다. **10R 한도 대비 0.887R**이 이 제안이 회수하는
전부다.

**그래서 이것은 급한 일이 아니다.** 급하지 않은 것을 급한 것처럼 만들지 않기 위해 여기 적는다.
다만 시간이 해결하지도 않는다 — 드로다운 오염은 영구적이고, 다음 라이브 무장 전에는 정리돼
있어야 한다.

## 5. 열린 질문 (Thomas 결정)

1. **SUPERSEDE를 쓰는가, VOID만 두는가.** §2-3은 SUPERSEDE를 권한다(VOID는 반대 방향으로
   틀림). 다만 SUPERSEDE는 "런타임이 기록하지 않은 숫자를 런타임이 믿는다"는 성질이 있고,
   그 숫자의 출처는 사람이 계산한 값이다. 승인 해시에 묶이지만 여전히 사람이 넣은 수다.
2. **정정 파일도 `record_sha256` 체인을 갖는가**, 아니면 행별 자체 해시만인가. 아웃컴 파일은
   후자다. 같게 가는 것이 일관되지만, 정정은 수가 적어 체인 비용이 낮다.
3. **`live_outcomes.jsonl`의 원본 행에 표식을 남기는가.** 남기지 않는 쪽을 권한다 — 남기려면
   append-only를 깨야 하고, 그것이 이 제안이 존재하는 이유다.
