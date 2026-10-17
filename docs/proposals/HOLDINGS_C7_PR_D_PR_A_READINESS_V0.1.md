# 제안: C7 PR-D·PR-A 구현 준비 — 코드 변경 지점, 테스트 전환, PR 사이의 이음매 (DRAFT v0.1)

**상태:** IMPLEMENTED 2026-10-17 — 이 노트가 준비한 두 PR이 지어졌다: PR-D #1216(2026-10-12 병합, 배포 HOLD), PR-A #1231(2026-10-17 병합, PR-B 전 배포 금지). 노트의 DECISION_REQUIRED 둘은 2026-10-17 Thomas가 정했다: R-2는 감지 전용 code, R-3는 (가-수정) basis 기록(`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.4). 아래는 작성 당시 기록이다. **원래 상태(작성 당시):** DRAFT 2026-10-11 — C7 PR-D(BLOCKED 알림)와 PR-A(체크포인트 감지 전용)를 짓기 전에, main `b1e48637`의 실제 코드에 대응시킨 준비 노트다. 설계의 권위는 `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`(C7)에 있고, 이 노트는 그것을 바꾸지 않는다. 구현은 시작하지 않았다. R-1–R-3의 검토 결과는 §2.3과 §3.4에 있다(2026-10-11 2차 검토). 권고는 검토 권고일 뿐이고, 알림 권한이나 영구 저장 계약을 확정하지 않는다. DECISION_REQUIRED가 둘 있다: R-2의 감지 단계 reason_code와 R-3의 체크포인트 소실 구분. 코드 착수 자체는 Thomas 승인 대기다.

**기준:** origin/main `b1e48637`(#1213), 2026-10-11. 코드는 읽기만 했다. 테스트는 합성 fixture로만 돌렸다. 실제 원장, 상태 파일, 거래소 API에는 접근하지 않았다.
**소유:** 설계·순서·결정은 C7 문서 §2–§12다. 순서는 PR-D → A → B → C이고(§10, D1 방향 동의), 이 노트는 그 순서를 바꾸지 않는다.

---

## 0. 한 줄

- **C7은 main에 한 줄도 없다.** `ledger_state_consistency`는 지금 사슬 검증, 상태 파일 판독, cutover 일치만 본다.
- **PR-D가 바꿀 곳은 `store.refresh`의 두 `except`와 `scheduler.py`의 알림 묶음이다.**
- **PR-A가 바꿀 곳은 `cash_flows._commit`의 `build`와 `readiness()`의 읽기 순서다.**
- **두 PR 사이에 확인해 둘 이음매가 있다.** C7 §6은 감지 단계(PR-A)의 알림 **키**를 다룬다(불일치 동안 멈춰 있는 체크포인트). 그러나 알림의 **입력**은 §6이 "`store.refresh`가 `ToolError`를 잡는 자리"로만 적는다. PR-A는 감지만 하므로 예외를 던지지 않는다. 그러므로 PR-D의 알림 입력은 예외 하나로 끝나면 안 된다(R-2). 1차 노트의 "설계 문서에 적히지 않은 이음매"라는 표현은 키까지 포함하는 것처럼 읽혀 과했으므로 이렇게 고친다.

---

## 1. 지금 상태 (작업 2, 2026-10-11)

### 1.1 구현 현황

| C7 PR | main에 있나 | 근거 |
|---|---|---|
| PR-0 (페이지 완전성) | **있음** (#1210) | `binance_wallet.flow_history`의 `FLOW_PAGES`와 `FLOW_INCOMPLETE` |
| PR-D (BLOCKED 알림) | 없음 | `store.py`에 BLOCKED 경로 없음. 원장 장애 때 reason_code 대신 예외 클래스 이름만 남는다 |
| PR-A (체크포인트 감지) | 없음 | `cash_flows.py`에 체크포인트 키 없음. "checkpoint" 1건은 #1212 진단 주석이 C7 문서를 가리키는 것이다 |
| PR-B (`history_completeness`, 재수집 판정) | 없음 | `history_completeness`, `history_assessed`, `toss_window_skipped` 0건 |
| PR-C (채택, 쓰기 거부) | 없음 | `adopt`, `ledger_adopted` 0건 |

### 1.2 결함 재현 테스트 (`tests/test_mvp_runtime_holdings_c7_partial_restore.py`, 15건 모두 통과)

| 테스트 | 지금 고정하는 것 | 기대값을 바꾸는 PR |
|---|---|---|
| `test_c7_1`, `_2`, `_3`, `_4`(겹침 안), `_5`, `_7`, `_9`, `_10`(2건), `_11`(첫 lookback 안) | 정상 또는 기존 거부 | 유지. 단, C7-4 겹침 안은 설계상 BLOCKED로 바뀐다(D1 포함 선택). PR-A에서는 FAIL과 알림, PR-C에서는 거부 |
| `test_defect_c7_d_a_refused_write_sends_no_message` | 사슬 손상 때 메시지 0 | **PR-D** |
| `test_defect_c7_4_…past_the_overlap…` | 유실하고 PASS | **PR-A**(FAIL과 알림) → **PR-C**(거부) |
| `test_defect_c7_6_…same_length…` | PASS | **PR-A** → **PR-C** |
| `test_defect_c7_8_…without_a_checkpoint…` | 일관된 것으로 읽음 | **PR-B**(`history_completeness` UNCHECKED). R-3 참고 |
| `test_defect_c7_11_…older_than_the_lookback…` | 유실하고 PASS | **PR-B** |

### 1.3 회귀 범위 (기준선)

`tests/test_mvp_runtime_holdings*.py`, `tests/test_policy_holdings_read_grant.py`, `tests/test_claude_local_only_guard.py`: **596 passed** (main `b1e48637`, 호스트 venv, worktree 코드).
CI의 Python 3.12 실행은 아니다.

### 1.4 H6b-shadow와 H6c의 실제 차단 조건 (코드: `cash_flows.readiness`)

- **H6b-shadow:** readiness의 모든 검사가 PASS여야 한다.
  - 검사 목록: `binance_access`, `binance_real_schema`, `cross_source_dedupe`, `current_health`, `cutover_boundary`, `internal_transfer_guard`, `ledger_chain`, `ledger_state_consistency`, `post_cutover_exceptions`, `shadow_observation`, `toss_cash_semantics`
  - 관찰 조건: `SHADOW_MIN_DAYS = 7`일, 그리고 7개 KST 날짜
  - 다 갖춰지면 `H6b_shadow_ready`가 텔레그램으로 한 번 알린다. 켜지는 것은 없고, 승인은 Thomas가 한다.
- **H6c:** H6 §7의 4단계, H6b-shadow 운영, Toss 현금 의미, 미해결 예외 0, **C7 구현·배포**(#1211 Q3). 각각 따로 승인한다.
- **PR-A의 영향:** PR-A가 `ledger_state_consistency`를 FAIL로 만들면 위 목록 중 하나가 FAIL이 되므로 `H6b_shadow_ready`도 막힌다. 의도된 동작이다.

---

## 2. PR-D 준비 — BLOCKED 알림과 reason_code 보존 (C7 §6)

### 2.1 지금 코드가 하는 일 (main `b1e48637`)

| 위치 | 동작 | 결과 |
|---|---|---|
| `store.refresh`, `collect_binance`를 감싼 `except Exception` | `transfers, flows_note = None, type(exc).__name__` | reason_code가 사라지고 클래스 이름("ToolError")만 남는다 |
| 같은 함수, `collect_toss`를 감싼 `except` | `flows_note = flows_note or f"toss {type(exc).__name__}"` | 같다 |
| 같은 함수, summary·readiness 블록의 `except Exception` | `summary()`가 `verify()`를 부르므로, 사슬이 깨지면 여기서 끝난다 | `update_readiness`와 `mark_fire_verified`가 돌지 않는다. 그래서 **어떤 알림도 만들어지지 않는다**(C7-D의 원인) |
| `scheduler.py`, holdings 유지보수 fire | `_tell` → 전달 성공 때만 `mark_*_told`. drawdown, readiness, exceptions 세 묶음 | 네 번째 묶음을 넣을 자리 |

### 2.2 변경 지점 (제안)

1. `store.refresh`: 세 `except`에서 `MvpRuntimeError`면 `exc.reason_code`를 남긴다. 그 밖의 예외는 지금처럼 클래스 이름을 남긴다. 스냅샷의 `cash_flows.error`에 그 값이 담긴다.
2. `store.refresh`: 원장·상태 블록이 실패해도 돌아가는 별도 판정 `blocked = cash_flows.blocked_state(state_dir, error=…)`를 둔다. 반환값에 `blocked_alert: (token, text) | None`을 더한다.
   - 대상: `LEDGER_TAMPERED` 계열만(C7 §6). `*_LOCKED`는 일시적이므로 제외한다.
3. told 파일 하나(holdings 상태 디렉터리, LOCAL_ONLY): `combined.write_told`와 같은 형식(`tmp` → `replace`)이다. 담는 것은 장애 키, 처음 본 시각, 그때의 원장 끝 시각이다.
4. `scheduler.py`: 네 번째 묶음을 넣는다. `_tell(text)`가 성공하면 `holdings_store.mark_blocked_told(token, …)`를 부른다. 전달에 실패하면 표시하지 않으므로 다음 fire가 다시 제안한다.
5. 해소 문구: 장애 키가 사라지면 1회 보낸다(D1의 포함 선택).
6. 문구에는 reason_code, 처음 본 시각, 남은 날, 다음 행동만 담는다. 금액·계정·자산은 담지 않는다.

### 2.3 확인이 필요한 설계 항목 — 2차 검토 결과 (2026-10-11)

#### R-1 알림 키: 장애 동안 바뀌지 않는 상태 파일의 digest (검토 권고)

C7 §6은 키를 `digest(reason_code, 멈춰 있는 상태 체크포인트 또는 "unreadable")`로 정했다. PR-D 시점에는 체크포인트가 없으므로, 그 자리에 상태 파일 바이트의 digest를 쓰자는 권고다.

| 확인 항목 | 결과 | 근거 |
|---|---|---|
| 같은 장애의 재시도에서 중복이 없나 | **없다.** 단, fire의 호출 순서가 유지될 때만이다 | 상태를 쓰는 경로는 둘이다. ① `_commit`(collect_binance, collect_toss, mark_fire_verified, set_cutover, resolve, start_semantics_epoch)은 사슬 재검증이 먼저라 장애 때 저장에 닿지 않는다. ② 상태만 쓰는 `update_readiness`, `mark_readiness_told`, `mark_exceptions_told`는 **원장을 검증하지 않고 저장한다**. 그런데 `store.refresh`에서 `summary()`가 `update_readiness`보다 먼저 실패하므로 fire에서는 저장이 일어나지 않는다. **PR-D는 이 순서를 바꾸지 말고, 순서가 지켜지는지 테스트로 고정해야 한다** |
| 해소 뒤 재발 때 새 알림이 나가나 | **나간다.** 다만 digest가 아니라 told 파일의 생애로 보장한다 | told 파일에 `{key, told_at, resolved_told_at}`을 둔다. 해소 문구를 보낸 키가 다시 나타나면 다시 알린다. 해소와 재발 사이에 성공한 쓰기가 없어 digest가 같아도 재발을 놓치지 않는다 |
| 상태 파일이 없거나 깨졌거나 읽을 수 없을 때 | **fail-closed.** 키 성분을 `missing`, `unparsable`, `unreadable` 중 하나로 두고 알린다. told 파일을 읽을 수 없으면 아직 알리지 않은 것으로 보고 다시 알린다(반복은 허용, 누락은 불허) | — |
| 민감 정보 | 상태 파일에는 토스 현금 값과 커서가 들어 있다. digest는 되돌릴 수 없고, 시각·해시 같은 고엔트로피 필드가 섞여 있어 추측도 어렵다. 그래도 **digest는 LOCAL_ONLY told 파일에만 둔다.** 메시지, 외부 스냅샷, 로그에는 넣지 않는다. 메시지에는 reason_code, 처음 본 시각, 남은 날, 다음 행동만 담는다 | — |
| PR-A 체크포인트 키로 바꿀 때 | 키에 형식 표지를 붙인다(`v1:state:<digest>`, `v2:ckpt:<줄 수>:<해시>`). 전환 순간에 장애가 이어지고 있으면 새 키로 **한 번 더 알린다**. 반복이고 누락이 아니므로 허용하는 쪽을 권한다 | — |

이 권고는 리뷰 항목이다. told 파일의 형식과 위치는 PR-D 리뷰에서 정한다. 이 노트는 영구 저장 계약을 확정하지 않는다.

#### R-2 예외 없이 감지된 BLOCKED 상태

| 입력 | 언제 | 알림 키 | reason_code |
|---|---|---|---|
| ① 원장·상태 검증 예외 | 사슬 손상, 상태 판독 불가, legacy 거부(지금 코드) | R-1(상태 digest). PR-A 뒤에는 멈춘 체크포인트 | 던져진 `exc.reason_code`(`LEDGER_TAMPERED` 계열) |
| ② PR-A의 감지 표지 | 체크포인트 불일치, told 키 유실(C7-4·C7-6) | **멈춘 체크포인트**(C7 §6). 감지 단계에서는 쓰기가 계속되므로 상태 digest를 쓰면 fire마다 키가 바뀌어 알림이 반복된다. **②에는 R-1의 digest를 쓰면 안 된다** | **DECISION_REQUIRED** |

- **②의 reason_code가 열려 있는 이유:** C7 §6은 BLOCKED 대상을 "`LEDGER_TAMPERED` 계열(사슬, 상태 판독 불가, 체크포인트, legacy 거부)"로 적었다. §3은 최종 동작(PR-C)에서 `LEDGER_TAMPERED`를 던진다고 적었다. 감지 단계에서 같은 코드에 부가 사유(`checkpoint`, `told_key`)를 붙일지, 감지 전용 코드를 따로 둘지는 소유 문서에 없다.
- **전달 방식:** 기존 "전달 성공 뒤에만 표시"(`scheduler.py`의 `_tell` → `mark_*_told`)를 그대로 재사용한다. 소유 문서 §6의 "토큰을 실어 보내고 전달된 뒤에만 표시"와 일치한다.
- **기록 위치:** ①은 기록할 것이 없다(예외 자체). ②의 감지 표지는 PR-A가 상태 캐시에 남긴다(§3.2). told 파일은 별도다(§6, 낙폭 told 파일과 같은 형식).
- **PR-A에 넣지 않는 것:** 쓰기 차단(PR-C), 자동 복구, cutover 재설정, 채택(PR-C).

#### 남은 날 (1차 노트에서 유지)

- C7 §6은 남은 날을 "처음 감지 때 기록한 원장 끝 시각 + 7일"로 정했다.
- 사슬이 깨진 원장의 끝 시각은 신뢰할 수 없다. 그래서 처음 감지할 때 마지막 줄의 `at`을 "검증되지 않은 값"으로 읽어 told 파일에 고정해 두고, 읽을 수 없으면 "알 수 없음"으로 알린다.
- 리뷰 항목이다.

### 2.4 fail-closed 정의

- 전달에 실패하면 표시하지 않고 다음 fire에 다시 보낸다. 반복은 허용하고 누락은 허용하지 않는다.
- told 파일을 쓸 수 없으면 다시 보낸다.
- BLOCKED 판정 자체가 실패하면(예: 상태 디렉터리를 읽을 수 없음) reason_code `unreadable`로 한 번 알린다.
- **원장·상태 파일에는 아무것도 쓰지 않는다.** BLOCKED 경로는 읽기와 told 파일 쓰기만 한다.
- readiness 판정은 바꾸지 않는다. readiness는 이미 `ledger_chain` FAIL이다.

### 2.5 테스트 계획 (합성만)

| 테스트 | 기대 |
|---|---|
| `test_defect_c7_d` → 이름을 바꾸고 기대값 전환 | 사슬 손상 fire에 `blocked_alert` 1개, 스냅샷 `cash_flows.error == LEDGER_TAMPERED` |
| 원장과 상태가 모두 깨짐 | 1회 전송 |
| 전달 실패 | 다음 fire에 다시 제안 |
| 전달 성공 | 같은 장애에 중복 없음 |
| 재시작 | told 파일이 유지되어 중복 없음 |
| `STATE_LOCKED`, `LEDGER_LOCKED` | BLOCKED 없음 |
| 장애 해소 | 해소 문구 1회 |
| 문구 | 숫자 금액, 자산명, 계정 식별자 없음(합성 금액 문자열이 출력에 없음을 검사) |
| R-2 | 상태의 감지 표지만으로도 알림(PR-A 전에는 표지를 테스트에서 직접 심음) |
| 도어 격리 | 새 told 파일 이름이 도어 모듈에 나타나지 않음(`test_no_door_module_reads_the_ledger`와 같은 형태) |

**테스트 명령 (worktree 기준):**
```
PYTHONPATH=$PWD .venv/bin/python -m pytest -q tests/test_mvp_runtime_holdings_c7_partial_restore.py tests/test_mvp_runtime_holdings_cash_flows.py tests/test_mvp_runtime_holdings_h6d_readiness.py
```

### 2.6 바뀌는 파일 (예상)

- `runtime/mvp_runtime/holdings/store.py`
- `runtime/mvp_runtime/holdings/cash_flows.py` (`blocked_state`, told 파일 읽기·쓰기)
- `runtime/mvp_runtime/scheduler.py` (네 번째 묶음)
- `tests/test_mvp_runtime_holdings_c7_partial_restore.py`, 새 테스트 파일 1개
- `docs/DIAGNOSTIC_CODE_INDEX.md` (새 reason_code가 생기면)
- history 파일

**운영 영향:** 장애 때만 메시지 1개가 나간다. 정상 운영에서는 나갈 일이 없다(C7 §10).

---

### 2.7 PR-D 구현 착수 체크리스트 (코드 변경 없음, 2026-10-11)

| # | 항목 | 내용 |
|---|---|---|
| 1 | 변경 예정 파일 | `holdings/store.py`, `holdings/cash_flows.py`(BLOCKED 판정과 told 파일 읽기·쓰기), `mvp_runtime/scheduler.py`(네 번째 알림 묶음), 테스트 2개, history. 새 reason_code가 생기면 `docs/DIAGNOSTIC_CODE_INDEX.md` |
| 2 | 재사용 | `combined.write_told`·`read_told`의 형식(tmp → replace), `scheduler._tell`과 "전달 뒤 표시", `store.refresh`의 예외 경계, `cash_flows.verify`, `load_state`, 테스트 `_store_fire` fixture |
| 3 | 새 함수가 필요한 이유 | 기존 세 알림은 모두 원장이 읽혀야 계산된다(`update_readiness`). BLOCKED는 원장이 깨졌을 때 나가야 하므로 그 밖에 있어야 한다. 함수는 둘이다: 읽기 전용 판정 `blocked_state(state_dir, error=…, marker=…)`, 그리고 전달 뒤에 부르는 `mark_blocked_told` |
| 4 | 중복 방지 계약 | 키(R-1·R-2)가 같으면 한 번. 전달된 뒤에만 표시. 해소되면 해소 문구 한 번, 재발하면 다시 알림 |
| 5 | 실패 시 안전 동작 | §2.4. 원장·상태에 쓰지 않는다. 판정이 실패하면 `unreadable`로 알린다. 전달 실패나 told 파일 쓰기 실패는 다음 fire에 다시 알린다 |
| 6 | 합성 테스트 | §2.5에 더해: fire 순서 고정(장애 중 `update_readiness`가 상태를 저장하지 않음), 재발 알림, 키 형식 표지, digest가 메시지·스냅샷에 나오지 않음 |
| 7 | PR-A 연결 계약 | `blocked_state`는 감지 표지(②)를 입력으로 받는 자리를 처음부터 둔다. ②의 키는 멈춘 체크포인트다(R-2) |
| 8 | 바뀌는 기존 동작 | 장애 때 스냅샷 `cash_flows.error`가 클래스 이름 대신 reason_code를 담는다. 장애 때 텔레그램 1건, 해소 때 1건. 정상 운영에서는 바뀌는 것이 없다 |
| 9 | 배포 전 필수 검증 | CI 필수 5개, holdings 회귀(기준선 596), C7 테스트 기대값 전환, 도어 격리 테스트, LOCAL_ONLY Guard 테스트, 문구에 금액이 없음 |
| 10 | Thomas 승인 요청 | (a) PR-D 구현 착수, (b) told 파일을 holdings 상태 디렉터리에 새로 두는 것(LOCAL_ONLY, 영구 저장 계약), (c) R-1 키 방식, (d) ②의 reason_code(R-2, PR-A 리뷰 때여도 됨) |

## 3. PR-A 준비 — 체크포인트 감지 전용 (C7 §3)

### 3.1 지금 코드가 하는 일

| 위치 | 동작 |
|---|---|
| `cash_flows._commit` | 상태 잠금 → 상태 읽기 → `_append_unique(build)`(원장 잠금, 사슬 재검증, 중복 제거) → `cutover_at` 캐시 → `patch` → `_save_state` |
| 모든 원장 쓰기 | `_commit`을 지난다. `_append`는 호출하는 곳이 없다 |
| 상태만 쓰는 함수 | `update_readiness`, `mark_readiness_told`, `mark_exceptions_told`. 셋 다 상태 잠금 아래에서 통째로 읽고 통째로 저장하므로, 새 키를 넣어도 보존된다(PR-A에서 고정 테스트) |
| `readiness()` | **원장(`verify`)을 먼저, 상태(`load_state`)를 나중에** 읽는다. 일관성은 `state_ok`, 사슬 PASS, `cutover_at` 일치뿐이다 |
| 원장 줄 | `chained_log`: 줄마다 `prev_sha256`과 `sha256`. N번째 줄의 해시가 1…N 전체를 증명한다 |

### 3.2 변경 지점 (제안, 설계 §3 그대로)

1. 상태 캐시에 `ledger_checkpoint = {"lines": N, "sha256": h_N, "at": now}`를 둔다. `_commit`이 append한 뒤 새 끝으로 옮긴다.
2. `build(existing)` 안, 즉 두 잠금 아래에서 판정한다.
   - 체크포인트가 있고 `len(existing) ≥ N`이며 `existing[N-1].sha256 == h`이면 정상이다.
   - 아니면 **감지만 한다.** 상태에 감지 표지(`ledger_checkpoint_mismatch = {reason, since}`)를 남기고, 체크포인트는 불일치 동안 옮기지 않으며, 쓰기는 계속한다.
3. 두 번째 감지기: 상태의 `exceptions.told_keys` 가운데 원장 예외 키와 맞지 않는 것이 있으면 유실이 증명된 것이다. 같은 표지를 남긴다.
4. `readiness()`: **상태를 먼저, 원장을 나중에** 읽는다(경합 오탐 방지, §3). 감지 표지가 있으면 `ledger_state_consistency = FAIL`이다.
5. PR-D의 `blocked_state`가 감지 표지를 입력으로 받는다(R-2).

### 3.3 감지할 복원 시나리오와 PR-A 이후의 기대

| 시나리오 | 테스트 | PR-A 이후 |
|---|---|---|
| 정상 연장 | C7-1, C7-3 | 정상 |
| append 뒤 저장 전 중단 | C7-2 | 체크포인트가 뒤처진 조상 → 정상 |
| 상태만 과거 | C7-3, C7-10 | 조상 → 정상, 옛 커서로 다시 읽음 |
| 원장만 과거(겹침 밖) | `test_defect_c7_4` | **FAIL + BLOCKED 알림**, 쓰기는 계속 |
| 원장만 과거(겹침 안) | C7-4 | 설계상 FAIL + 알림(D1 포함 선택. 오늘은 자동 복구) |
| 같은 길이의 다른 원장 | `test_defect_c7_6` | **FAIL + 알림** |
| 둘 다 같은 시점에서 복원 | C7-5 | 체크포인트가 원장과 맞으면 정상. 그 앞의 이력은 PR-B |
| 체크포인트 없음 | `test_defect_c7_8` | 쓰기 허용, 관찰 체크포인트 기록. 이력 판정은 PR-B(R-3) |
| 상태 파일 누락 | C7-11 | 같음. 재수집 판정은 PR-B |
| 사슬 손상 | C7-9 | 기존 `LEDGER_TAMPERED` 거부. PR-D 알림 |
| 재읽기 중복 | C7-10 | 중복 없음(기존 키) |

**PR-A가 하지 않는 것:**
- 원장 줄을 새로 만들지 않는다. 바뀌는 것은 상태 키뿐이다.
- 쓰기 거부, 채택, 복구, 재수집을 하지 않는다. 쓰기 거부와 채택은 PR-C, 재수집 판정은 PR-B다.

### 3.4 확인이 필요한 설계 항목 — 2차 검토 결과 (2026-10-11)

#### R-3 첫 체크포인트 이전, 그리고 체크포인트 소실

- **구분이 필요한가: 그렇다.** "체크포인트가 한 번도 없었던 상태(PR-A 배포 직후)"와 "있었는데 사라진 상태"는 의미가 다르다. 사라진 경우는 PR-A 이전 백업에서 상태를 복원했거나, 상태 파일을 새로 만든 경우다.
- **지금 설계로는 구분할 수 없다.** 체크포인트는 상태 캐시에만 산다(§3.2). 상태만 보면 둘은 똑같이 "키 없음"이다. 원장에는 체크포인트의 흔적이 없다.
- **구분할 수 있게 하는 선택지 (DECISION_REQUIRED):**
  - (가) 구분하지 않는다. 체크포인트 없음은 모두 PR-B의 `history_completeness`가 UNCHECKED로 드러낸다(D2의 결정과 일치). PR-A는 원장 이벤트를 더하지 않는다.
  - (나) PR-A가 첫 체크포인트를 만들 때 원장에 줄 하나(`checkpoint_introduced`)를 남긴다. 원장에 그 줄이 있는데 상태에 체크포인트가 없으면 "사라짐"이므로 감지 표지를 남긴다. 대가로 PR-A가 새 원장 이벤트 종류를 갖게 되고, D1의 구버전 호환 합성 테스트가 PR-A에도 필요해진다.
- **`ledger_state_consistency`의 의미는 바꾸지 않는다.** 체크포인트가 없을 때는 지금 판정(사슬, 상태 판독, cutover 일치)을 그대로 쓴다.
- **수용 기준 (확실하지 않으면 검증 완료라고 쓰지 않는다):**
  - 체크포인트가 없는 상태에서 `ledger_state_consistency`가 PASS여도, 그것을 "복원 안전성 검증"으로 표시하지 않는다. readiness 상세에 `checkpoint: absent`를 적는다.
  - PR-A와 PR-B 사이 기간에는 "사라진 체크포인트"가 PASS로 읽힐 수 있다. 이것을 PR-A의 알려진 한계로 PR 본문과 history에 적는다. (나)를 고르면 이 한계가 없어진다.
  - `history_completeness`는 PR-B의 책임으로 남는다.
- **경합:** 감지 표지를 남기는 쓰기와 `update_readiness`의 상태 저장은 같은 상태 잠금을 쓰므로 순서가 보장된다(코드로 확인).

### 3.5 롤백

- PR-A는 상태 키만 더한다. 옛 코드는 상태를 통째로 저장하므로 키를 보존하지만 옮기지는 않는다.
- 다시 전진하면 옛 체크포인트는 그동안 자란 원장의 조상이므로 정상으로 이어진다(C7 §3).
- **새 원장 이벤트 종류는 PR-A에 없다.** 단, R-3에서 (나)를 고르면 `checkpoint_introduced` 한 종류가 생기고, 그 경우 구버전 호환 테스트가 PR-A에도 필요하다. D1의 "새 이벤트 종류의 구버전 호환 합성 테스트"는 PR-B(`history_assessed`, `toss_window_skipped`)와 PR-C(`ledger_adopted`)에서 적용된다. PR-A에서는 "옛 쓰기 함수가 새 상태 키를 보존한다"를 고정한다.

### 3.6 테스트 계획 (합성만)

- `test_defect_c7_4`, `test_defect_c7_6` → 이름을 바꾸고 기대값 전환(FAIL, 감지 표지, 알림. 쓰기는 계속)
- C7-1·2·3·5·7·9·10 유지
- 새 테스트:
  - 체크포인트 기록·이동
  - 불일치 동안 체크포인트가 멈춤
  - told 키 감지기
  - readiness가 상태를 먼저 읽음
  - 상태만 쓰는 세 함수와 `collect_binance` patch가 체크포인트 키를 보존함
  - 체크포인트 없는 배포 직후의 판정(R-3)

**바뀌는 파일 (예상):** `runtime/mvp_runtime/holdings/cash_flows.py`, `tests/test_mvp_runtime_holdings_c7_partial_restore.py`, 새 테스트 파일 1개, history 파일.

---

## 4. 이 노트 뒤에 필요한 승인

| 작업 | 필요한 승인 | 승인 전 상태 | 미승인 시 영향 | 다음 재개 지점 |
|---|---|---|---|---|
| PR-D 코드 착수 | Thomas: PR-D 구현 승인(D1의 "PR마다 따로") | 이 노트까지 | 원장 장애가 조용히 지나간다(C7-D) | §2.2와 R-1·R-2 리뷰 |
| PR-A 코드 착수 | Thomas: PR-A 구현 승인. PR-D 뒤 | 이 노트까지 | 섞인 복원이 PASS로 남는다(C7-4·C7-6) | §3.2와 R-3 리뷰 |
| PR-B | **D2 결정이 먼저**(별도 패킷) | 미착수 | `history_completeness` 없음 | D2 결정 패킷 |
| PR-C | PR-B 뒤 | 미착수 | 불일치 때 쓰기가 계속된다 | — |

## 5. 이 노트가 바꾸지 않는 것

런타임, 원장 형식, 상태 파일, readiness 판정, cutover, epoch, H6b-shadow·H6c 상태, 스케줄을 바꾸지 않는다. C7 설계의 결정(D1–D4)이나 순서도 바꾸지 않는다.
실제 원장, 상태 파일, 거래소 API에는 닿지 않았다.
