# 제안: C7 PR-D 배포 차단 조건 해소 — 검증 설계와 운영자 승인 패킷 (DRAFT v0.1)

**상태:** DRAFT 2026-10-12 — PR-D 배포를 막는 조건 셋을 푸는 방법의 설계다. 셋은 Gate 2의 told 파일명 직접 합성 검증, 이미지 내부 동작 검증 경로, Candidate·Rollback 연결 검증이다. 2026-10-12 갱신: Gate 2는 kit 직접 합성 시험(`ALL PASS`, exit 0)을 Thomas가 수용해 충족됐다(GATE_2_FULLY_SATISFIED, GATE_2_DIRECT_SYNTHETIC_TEST_PASSED). 새 세션 told 실탐침은 하지 않았다(NEW_SESSION_TOLD_PROBE_NOT_PERFORMED). 자가검증 진입점 `scripts/c7_blocked_selftest.py`를 구현했고 저장소 합성 테스트를 통과했다(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED, §2.6). candidate 안 실행과 D-10 격리 증거는 아직이다. 남은 상태: VALIDATION_PATH_UNRESOLVED, ROLLBACK_NOT_VERIFIED, CANDIDATE_TAG_DECISION_REQUIRED, DEPLOYMENT_HOLD, NOT_GRANTED. 권고는 셋이다. Gate 2는 가안(Guard kit 시험 확장). 이미지 검증은 인자 없는 정식 자가검증 진입점(VALIDATION_PATH_DESIGN_PROPOSED, 정책 판단 필요, D-10 격리 증거 정의 포함). Rollback은 세 상태로 나눈다(ROLLBACK_TARGET_VERIFIED, ROLLBACK_PROCEDURE_REVIEWED, ROLLBACK_FUNCTIONAL_VERIFIED). Candidate 태그는 최종 배포 기준선이 정해진 뒤 정한다(`candidate-1217` — PROVISIONAL_RECOMMENDATION. 자가검증 PR이 묶음에 들어오므로 다시 정한다). Gate 2가 완전히 충족되지 않으면 배포는 막힌다(GATE_2_NOT_FULLY_SATISFIED → DEPLOYMENT_HOLD). 이 해석은 이제 준비 문서에도 반영됐다. 각 단계의 구현과 실행은 따로 Thomas 승인을 받는다.

**기준:** origin/main `52e2b3ba`(#1219), 2026-10-12. 저장소 코드·문서, `/root/.claude/global-guard-kit`의 README·`test_guard.py`·`refresh_guard.py`를 읽기만 했다. Guard 탐침, kit 실행·변경, `docker run`/`exec`, 이미지 빌드·태그, 상태 파일·원장·Secret 접근은 없었다.
**소유:** 배포 준비의 기준선과 Gate 정의는 `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md`에 있다. 이 문서는 그 Gate를 바꾸지 않고, 각 Gate를 채울 방법만 설계한다. Guard 정책은 `.claude/hooks/guard-local-only.sh`와 그 테스트가 정한다.

---

## 0. 한 줄

- **Gate 2:** 가안을 권고한다. Thomas의 Guard kit 시험(`test_guard.py`)에 told 파일명 DENY 3건과 `holdings_snapshot.json` ALLOW 1건을 더한다. 이 시험은 Claude Code가 부르는 그대로 **전역 설정의 wrapper와 설치본**을 통과한다. 나안은 저장소 테스트 코드를 고쳐야 하고, 전역 연결은 검증하지 못한다.
- **이미지 검증:** 인라인 import를 스크립트로 옮기는 것은 경로가 아니다. 정식 진입점은 다음을 모두 갖출 때만 경로 후보가 된다.
  - 인자가 없다.
  - 자기가 만든 임시 디렉터리에서 합성 데이터만 쓴다.
  - 결과는 고정된 PASS/FAIL 단어뿐이다.
  - 저장소 테스트로 고정된다.
  - 격리된 컨테이너에서 candidate에만 돌린다.
  - 격리는 옵션이 아니라 실행 직전 컨테이너의 실제 구성으로 증명한다(§2.5 D-10 ISOLATION EVIDENCE).
  
  이것이 Guard 정책을 지키는지는 Thomas의 판단이다. 그 판단과 구현·실행 승인 전까지 VALIDATION_PATH_UNRESOLVED다. 설계가 있다는 뜻의 VALIDATION_PATH_DESIGN_PROPOSED와는 다른 상태다.
- **Candidate·Rollback:** 승인 SHA → 배포 worktree → candidate 이미지 id → 이미지 안 소스 해시 → rollback 태그 id = 배포 전 여덟 서비스의 이미지 id. 이 연결을 단계마다 기록한다. 태그는 최종 배포 기준선이 정해진 뒤 정한다(`candidate-1217`은 PROVISIONAL_RECOMMENDATION, §3.5). Rollback은 대상 확인, 절차 검토, 실제 복구 성공을 따로 기록한다(§3.3). 실제 복구 훈련은 권하지 않는다.

## 1. Gate 2 — told 파일명 직접 합성 검증

### 1.1 무엇이 비어 있나

준비 문서의 Gate 2는 "told 파일명 합성 DENY, `holdings_snapshot.json` 합성 ALLOW"를 운영 Global Guard에서 요구한다. 지금 증거는 두 가지뿐이다.
- 설치본과 main의 바이트 동일성
- main CI 테스트(`test_the_told_record_is_refused_by_name_wherever_it_is`, `test_the_told_rule_leaves_ordinary_work_alone`)

이것은 간접이다(TOLD_FILENAME_PROTECTION_VERIFIED_BY_CODE_IDENTITY_AND_CI). 운영 Guard에 대한 직접 합성 검증은 하지 않았다(DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED, 작성 당시 기록).

**갱신(2026-10-12):** §1.4의 G2-1–G2-4를 수행했다.
- kit `test_guard.py`: sha256 `1922dcfe…` → `d1ae8d77055d3799ce5eb98701c84b45780c1410bd1cb8016e94bf1b82c9dab4`, 사본 `test_guard.py.pre-told`
- 결과: 설정 6/6, DENY 15+3, ALLOW 10+1, Guard가 없을 때의 동작 3/3, `ALL PASS`, exit 0, 실행 전후 설치본·kit·설정 sha 불변

Thomas가 이를 Gate 2의 직접 합성 검증으로 수용했다(GATE_2_FULLY_SATISFIED, GATE_2_DIRECT_SYNTHETIC_TEST_PASSED). 이전 레이블 DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED는 이것으로 대체됐다. 남는 미수행은 새 Claude 세션의 told 실탐침뿐이다(NEW_SESSION_TOLD_PROBE_NOT_PERFORMED, 필수 아님). kit 합성 시험과 같은 증거로 적지 않는다.

### 1.2 가안 — Guard kit 시험 확장 (권고)

**무엇을 바꾸나:** `/root/.claude/global-guard-kit/test_guard.py`의 두 목록에 네 줄을 더한다. 다른 줄은 바꾸지 않는다.

```text
# DENY 목록 끝에 추가 (경로는 존재하지 않는 합성 경로, 이름 규칙만 시험)
    ("Bash", bash("Bash", "cat /tmp/__synthetic__/holdings_cash_flow_blocked_told.json")),
    ("Read", {"tool_name": "Read", "tool_input": {"file_path": "/tmp/__synthetic__/holdings_cash_flow_blocked_told.json"}}),
    ("Monitor", {"tool_name": "Monitor", "tool_input": {"command": "tail -f /tmp/__synthetic__/holdings_cash_flow_blocked_told.json"}}),
# ALLOW 목록 끝에 추가 (바깥으로 나가도 되는 한 표의 이름)
    ("Bash", bash("Bash", "cat holdings_snapshot.json")),
```

| 검토 항목 | 판단 |
|---|---|
| 정책 변경 | 없다. Guard 규칙과 설정은 그대로이고, 이미 설치된 규칙을 시험할 뿐이다 |
| 합성 입력만으로 되나 | 된다. 경로가 상태 디렉터리 밖(`/tmp/__synthetic__/`)이라 경로 규칙은 걸리지 않고, #1217의 이름 규칙만 거부할 수 있다. 시험은 명령 텍스트만 hook에 넘기고 파일을 열지 않는다 |
| 무엇을 통과하나 | `test_guard.py`는 `/root/.claude/settings.json`의 PreToolUse wrapper를 꺼내 bash로 돌린다. 즉 **전역 설정 + 설치본**을 Claude Code가 부르는 그대로 시험한다 |
| 기존 시험과의 호환 | `pick(DENY, "holdings_local.json")`, `pick(DENY, "--full")`, `pick(ALLOW, "git status")` 같은 기존 조회와 겹치는 문자열이 없다. 결과는 여전히 `ALL PASS` 하나로 판정된다. 목록은 DENY 15 → 18, ALLOW 10 → 11 |
| `--project-tree` 모드 | 쓰지 않는 것이 기본이다. 현재 main의 worktree를 넘기면 그 저장소 hook도 같은 판정을 낸다(저장소 테스트로 확인된 동작). "어느 한쪽이라도 deny면 deny" 규칙과도 충돌하지 않는다. 반면 primary checkout(`/root/thomas_agent`, `f8efbe2f`에 멈춤)에는 project hook이 없어 더하는 것이 없다. README 예시의 `/root/wt-h6d-min`도 main이라는 보장이 없다 |
| 운영 부작용 | 없다. 시험은 설정 파일 읽기와 bash에 합성 JSON 넘기기뿐이다. 설치본, 설정, 상태를 쓰지 않는다 |
| 이름이 어긋날 위험 | kit은 파일명을 하드코딩한다. 저장소가 told 파일명을 바꾸면 kit 시험은 옛 이름을 계속 통과시킨다. 완화책은 둘이다. 저장소 테스트가 상수에서 이름을 읽어 CI에서 잡는다. 그리고 kit 갱신 절차에 "저장소 상수와 kit 이름 대조" 한 줄을 넣는다(1.4 G2-1) |
| Rollback | kit은 git 관리가 아니다. 바꾸기 전에 새 이름으로 사본을 둔다(1.4 G2-2). 되돌릴 때는 그 사본을 덮어쓴다 |
| 별도 승인 범위 | ① kit 파일 변경(Thomas 소유 경로). ② 변경 뒤 kit 시험 실행. ③ 선택: 새 세션 실탐침 |

**선택 — 새 세션 실탐침:** kit README의 "누가 대답하나" 방식 그대로, 존재하지 않는 합성 경로 `cat /tmp/__synthetic__/holdings_cash_flow_blocked_told.json`를 새 Claude 세션에 시킨다. hook의 "LOCAL_ONLY holdings data …"가 대답해야 한다. 실제 Claude Code 호출 경로를 지나는 유일한 직접 증거다. kit 시험이 같은 wrapper를 쓰므로 필수는 아니다.

### 1.3 나안 — 저장소 Guard 테스트를 설치본에 적용

**지금은 그대로 쓸 수 없다.** `tests/test_claude_local_only_guard.py`는 hook 경로를 `REPO_ROOT/.claude/hooks/guard-local-only.sh`로 고정한다. 설치본을 시험하려면 둘 중 하나가 필요하다.
- 테스트 코드에 경로 재지정 수단(예: 환경 변수)을 더한다. 저장소 코드 변경이고, PR과 별도 승인이 필요하다.
- 설치본을 저장소 checkout으로 복사한다. Global Guard 복사이므로 이 설계에서 제외한다.

| 항목 | 판단 |
|---|---|
| 장점 | 저장소 테스트 100건 전체를 설치본에 돌린다. told 파일명은 저장소 상수에서 읽으므로 이름이 어긋날 일이 없다 |
| 한계 | 저장소 테스트의 wrapper 검사는 **저장소** `settings.json`을 쓴다. 전역 설정의 연결은 검증하지 못하고, 그것은 kit만 한다. 바이트 동일성이 이미 확인된 지금은 사실상 같은 파일을 한 번 더 시험하는 셈이라, 새로 얻는 증거가 적다 |
| 실행 조건 | host venv와 깨끗한 main worktree 안에서 pytest를 돌려야 한다(host venv는 primary checkout을 import하는 함정이 있다) |
| 별도 승인 범위 | ① 테스트 코드 변경 PR. ② 설치본 대상 실행 |

### 1.4 권고와 단계

**권고: 가안.** 운영 Guard의 필수 검증을 반복 가능한 공식 kit 시험으로 관리할 수 있다. 전역 wrapper까지 지나간다. 저장소 코드를 바꾸지 않는다. 나안은 가안과 충돌하지 않으니, 나중에 저장소 테스트에 경로 재지정이 생기면 보강으로 더할 수 있다.

| 단계 | 실행 주체 | 승인 | 예상 증거 | 중단 조건 |
|---|---|---|---|---|
| G2-1 이름 대조 | Thomas | 불필요(읽기) | 저장소 main의 `BLOCKED_TOLD_FILENAME` 값이 kit에 넣을 이름과 같음 | 다르면 중단하고 kit 문안을 다시 만든다 |
| G2-2 kit 사본 | Thomas | **필요** | `test ! -e test_guard.py.pre-told && cp -p test_guard.py test_guard.py.pre-told`가 exit 0, 사본 sha256 = 원본 | 사본 이름이 이미 있거나 sha가 다르면 중단 |
| G2-3 kit 변경 | Thomas (또는 승인 시 Claude) | **필요** | 1.2의 네 줄만 추가. `diff` 결과가 그 네 줄뿐 | 다른 줄이 바뀌었으면 사본으로 되돌림 |
| G2-4 kit 시험 | Thomas | **필요** | `--project-tree` 없이 `python3 test_guard.py`를 돌린다(전역 설정 + 설치본만). `ok` 줄에 새 DENY 3건·ALLOW 1건이 있고, `ALL PASS`, exit 0 | `FAIL`이 하나라도 있으면 사본으로 되돌리고 보고. 설치본은 건드리지 않는다 |
| G2-5 새 세션 탐침(선택) | Thomas가 새 Claude 세션에 지시 | **필요** | hook이 거부 메시지로 대답 | 다른 대답이면 보고 |
| G2-6 판정 | Claude가 증거 판독 | — | G2-1–G2-4 통과 시 Gate 2 직접 합성 조건 충족을 **제안** | Thomas가 받아들이기 전까지 DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED 유지. **결과(2026-10-12): 모두 통과, Thomas 수용 → GATE_2_FULLY_SATISFIED** |

## 2. 이미지 내부 동작 검증 경로

### 2.1 충돌의 내용

배포 스킬 5단계는 candidate에서 `python -c "…"`로 바뀐 동작을 호출하라고 한다. PR-D의 동작은 `runtime.mvp_runtime.holdings` 안에 있다. Guard는 holdings 패키지를 import하는 인라인 Python을 거부한다(`inline Python importing the holdings package`). 이 규칙의 목적은 Claude 세션이 패키지의 읽기 함수를 통해 LOCAL_ONLY 데이터에 닿지 못하게 하는 것이다. Guard 헤더는 "디스크에 쓴 스크립트를 실행하면 통과한다"를 알려진 빈틈으로 적는다. 그러므로 **같은 import를 파일로 옮기는 것은 그 빈틈을 쓰는 우회이고, 경로가 아니다.**

### 2.2 정식 검증 진입점과 우회를 가르는 기준

진입점이 다음을 **모두** 만족할 때만 정식 경로 후보로 본다. 하나라도 빠지면 우회와 다르지 않다.

| # | 기준 | 우회와 다른 점 |
|---|---|---|
| D-1 | **입력 면이 없다.** 인자, 환경 변수, 표준 입력을 받지 않는다. 무엇이든 받으면 시작 전에 거부한다 | 실행 시점에 무엇을 읽을지 고를 수 없다 |
| D-2 | **자기 합성 루트만 쓴다.** `tempfile.TemporaryDirectory()`로 만든 루트를 모든 호출에 명시한다(`store.state_dir(root)` 등). 기본 상태 경로(`STATE_REL`)를 쓰지 않는다. 루트가 실제 상태 디렉터리 아래면 FAIL로 끝낸다 | 실제 LOCAL_ONLY 파일에 닿는 코드 경로가 없다 |
| D-3 | **합성 fixture만 쓴다.** 원장 줄, 상태, 장애를 스스로 만든다. 거래소·Toss 피드는 메모리 안 가짜다 | 실제 계좌 데이터가 들어올 수 없다 |
| D-4 | **네트워크·전송이 없다.** operator 채널, Telegram, provider, HTTP 모듈을 import하지 않는다. 알림은 메모리 기록기로 받는다 | 실제 Telegram이나 API가 불릴 수 없다 |
| D-5 | **Secret이 필요 없다.** env를 읽지 않는다(D-1) | `--env-file` 없이 돈다 |
| D-6 | **출력이 닫혀 있다.** 고정 단어 `PASS <검사명>` / `FAIL <검사명>`, 덮는 소스 파일의 sha256, 마지막 `RESULT PASS|FAIL`뿐이다. 키·digest·금액·경로 내용은 출력하지 않는다 | 결과로 정보가 새지 않는다 |
| D-7 | **코드가 고정·검토된다.** `scripts/` 아래 새 모듈로 PR 검토와 병합을 거친다. 실행은 candidate 이미지 안의 병합된 코드뿐이다 | 실행 내용을 세션이 바꿀 수 없다 |
| D-8 | **저장소 테스트로 고정된다.** AST·동작 테스트가 D-1–D-6을 검사한다: 기본 상태 경로 미사용, 금지 import 없음, 인자 거부, 출력 어휘 | 나중의 수정이 기준을 깨면 CI가 실패한다 |
| D-9 | **Runtime을 바꾸지 않는다.** runtime 코드는 이 모듈을 import하지 않는다. scheduler, door, 거래 경로와 무관하다 | 운영 동작과 권한에 영향이 없다 |
| D-10 | **격리 실행만 한다.** 별도 승인 아래 candidate에서만 돈다. 후보 옵션은 다음과 같다.<br>`--network none`, `--read-only`, `--user 10001:10001`, `--cap-drop ALL`, `--security-opt no-new-privileges`. 상태 루트(`/app/.runtime_governance_state`)와 `/tmp`에는 `--mount type=tmpfs`를 쓰고, 상태 루트는 `tmpfs-mode=0555`로 둔다. bind mount, named volume, `-e`, `--env-file`은 쓰지 않는다.<br>**옵션을 썼다는 사실은 격리의 증거가 아니다.** 증거는 §2.5의 D-10 ISOLATION EVIDENCE다 | 운영 상태 디렉터리와 완전히 분리된다. 단, §2.5로 실제 Mount·네트워크·권한·환경을 확인한 뒤에만 그렇게 말한다 |

**선례:** Guard는 이미 `holdings_board`(기본)와 `--json`을 허용한다. 둘 다 출력이 정해진, 검토된 정식 문이다. 저장소에는 임시 디렉터리 안에서만 도는 격리 자가검증 `scripts/self_test_core_release_flow.py`도 있다. 위 기준은 이 두 선례의 성질을 PR-D 검증에 옮긴 것이다.

**판단:** 위 기준을 모두 만족하는 진입점은 Guard 정책(LOCAL_ONLY 데이터 접근 차단)을 지키는 정식 인터페이스로 **설계할 수 있다**고 본다. 다만 두 가지는 이 문서가 정할 수 없다.
- 이것이 "인라인 import 규칙의 취지에 맞는 문"인지는 정책 판단이고, Thomas가 정한다.
- 구현과 실행은 각각 별도 승인이다.

**두 상태를 구분한다.**
- **VALIDATION_PATH_DESIGN_PROPOSED**는 이 설계가 있다는 뜻이다.
- **VALIDATION_PATH_UNRESOLVED**는 배포를 막는 상태이고, 그대로다.

D-1–D-10을 만족한다는 사실만으로 Guard 정책 준수가 자동 승인되지는 않는다. Guard는 인라인 holdings import를 막고, 파일에 쓴 스크립트 실행을 알려진 빈틈으로 적고 있다. 그러므로 진입점은 다음을 모두 거쳐야 경로가 된다.
- Guard를 피하려는 스크립트가 아니다.
- 실제 LOCAL_ONLY 데이터에 닿지 않는다.
- 검토된 제한적 인터페이스다.
- 정책 승인, 구현 승인, 실행 승인을 각각 받는다.

Guard 예외나 규칙 완화는 후보에 없다. Guard는 이 진입점 때문에 바뀌지 않는다.

### 2.3 진입점 설계 (구현하지 않음)

- **이름(안):** `scripts/c7_blocked_selftest.py`, 실행은 `python -m scripts.c7_blocked_selftest`. 명령 텍스트에 holdings 패키지 이름이 없다. 그러나 그 사실이 근거는 아니다. 근거는 2.2의 기준이다.
- **검사(합성 lane 하나, 저장소 테스트에서 고른 핵심 전이):**

| 검사명 | 내용 | 기대 |
|---|---|---|
| `healthy_is_quiet` | 정상 합성 원장 | 판정 정상, 알림 없음, told 파일 없음 |
| `broken_is_told_once` | 사슬 훼손 → 판정 → 기록 → 재판정 | BLOCKED 1회, 두 번째는 알림 없음 |
| `repair_clears_once` | 원래 원장 복원 | 해소 1회, 그 뒤 없음 |
| `vanished_is_not_cleared` | 알린 뒤 원장·상태 삭제 | 해소 보류(`clearing withheld`) |
| `stale_token_refused` | 더 이른 `now`의 기록 시도 | False, 기록 불변 |
| `lock_is_not_blocked` | 상태 잠금을 잡은 채 판정 | BLOCKED 아님 |
| `no_secret_in_text` | 알림 문구 | 키·digest·금액 없음 |

- **코드 식별 출력:** 덮는 파일(`runtime/mvp_runtime/holdings/cash_flows.py`, `store.py`, `scheduler.py`, `scripts/holdings_board.py`, 자기 자신)의 sha256을 출력한다. 승인 SHA의 git 파일과 대조해 candidate와 승인 코드를 잇는다(조건 7).
- **저장소 고정 테스트(같은 PR):**
  - 2.2의 D-1, D-2, D-4, D-6, D-8을 AST·동작으로 검사한다.
  - pytest에서 진입점을 직접 돌려 `RESULT PASS`를 확인한다.
  - Guard 테스트의 ALLOW 목록에 진입점 명령을 더하는 것은 Guard 테스트 변경이다. 같은 PR의 별도 판단으로 둔다.
- **CI 보강(선택):** Docker smoke에 같은 진입점을 한 단계 더하면, 모든 main 이미지가 진입점을 통과하는지 GitHub runner(빈 상태)에서 반복 확인된다. CI는 원래 holdings 테스트를 돌리는 기존 계약 안이다. 그러나 "다른 실행기로 옮기기"로 보일 수 있으므로 Thomas 판단 항목으로 둔다.

### 2.4 단계

| 단계 | 실행 주체 | 승인 | 예상 증거 | 중단 조건 |
|---|---|---|---|---|
| V-1 정책 판단 | Thomas | **필요** | 2.2 기준을 만족하는 진입점을 정식 경로로 인정할지 결정 | 인정하지 않으면 VALIDATION_PATH_UNRESOLVED 유지, 배포 HOLD |
| V-2 구현 PR | Claude | **필요** | 진입점 + 고정 테스트, 필수 CI 5개 PASS, Guard 테스트 그대로 PASS | 기준 하나라도 테스트로 고정 못 하면 중단·보고 |
| V-3 병합 | Thomas | **필요** | squash, 트리 일치 | — |
| V-4 candidate 격리 실행 | Thomas 또는 승인된 Claude | **필요** | §2.5의 증거 13항이 모두 맞고 `RESULT PASS`, exit 0, 소스 sha256 = 승인 SHA의 파일 | §2.5.1의 격리 실패 조건 중 하나, `FAIL`, 출력 어휘 밖의 줄이면 중단. 배포하지 않음. VALIDATION_PATH_UNRESOLVED 유지 |
| V-5 판정 | Claude가 증거 판독 | — | V-4 PASS + C-4(3.2) 일치 시 SYNTHETIC_RUNTIME_VALIDATION 충족을 **제안** | Thomas 수용 전까지 VALIDATION_PATH_UNRESOLVED |

### 2.5 D-10 ISOLATION EVIDENCE (증거 정의, 지금 실행하지 않음)

Dockerfile에는 `VOLUME ["/app/.runtime_governance_state"]`가 있다. 그 디렉터리는 `chown -R thomas:thomas /app`로 uid 10001의 것이 된다.
- 명시적 mount가 없으면 Docker는 이 경로에 **uid 10001이 쓸 수 있는 익명 볼륨**을 만든다.
- 이 볼륨은 `--read-only`를 빠져나간다.

그래서 실행 옵션만으로는 격리를 말할 수 없다. 격리는 **실행 직전 컨테이너의 실제 구성**으로 확인한다.

**확인 방식(설계):**
1. `docker create`로 컨테이너를 만들고, 시작하기 전에 `docker inspect`로 아래 항목을 읽는다.
2. 모두 맞을 때만 `docker start -a`로 실행한다.
3. 끝나면 `docker rm`으로 지운다.

모든 단계는 별도 실행 승인을 받는다. Secret 값은 출력하지 않는다. 환경은 **이름만** 읽는다(`{{range .Config.Env}}{{index (split . "=") 0}} {{end}}` 같은 형식).

| 검증 항목 | 필요한 증거 | 기대 |
|---|---|---|
| Image | `.Image` | 승인된 candidate 이미지 id(C-6의 Q) |
| Source | 진입점의 소스 sha256 출력 | 승인 Git SHA의 파일과 같음(C-7) |
| User | `.Config.User` | `10001:10001`(root 아님) |
| Privileged | `.HostConfig.Privileged` | `false` |
| Capabilities | `.HostConfig.CapDrop`, `.HostConfig.CapAdd` | `CapDrop`이 `ALL`, `CapAdd`는 비어 있음, `SecurityOpt`에 `no-new-privileges` |
| Network | `.HostConfig.NetworkMode` | `none`. 이 모드에서는 loopback 외의 인터페이스가 없다. Telegram, 거래소, 내부 서비스 어디에도 닿을 수 없다 |
| Mounts | `.Mounts`, `.HostConfig.Mounts` | 정확히 두 개, 둘 다 `Type=tmpfs`: `/tmp`, 그리고 `/app/.runtime_governance_state`(mode 0555) |
| Volume | `.Mounts`의 `Type` | `bind`, `volume`이 하나도 없음. 상태 루트에 tmpfs가 명시돼 있어 익명 볼륨이 생기지 않았음을 이 목록으로 확인 |
| Tmpfs | 진입점의 합성 루트 경로 출력(경로만) | `/tmp/` 아래. 상태 잠금 파일(`…/holdings/*.lock`)도 그 아래. 끝나면 `TemporaryDirectory`가 지우고, tmpfs는 컨테이너와 함께 사라짐 |
| Environment | 환경 변수 **이름** 목록 | Dockerfile 기본 ENV 다섯 개(`PYTHONUNBUFFERED`, `PYTHONUTF8`, `PYTHONDONTWRITEBYTECODE`, `PIP_NO_CACHE_DIR`, `PIP_DISABLE_PIP_VERSION_CHECK`)와 Docker 기본(`PATH`, `HOSTNAME` 등)만. 주입된 변수 없음. 진입점은 env를 읽지 않음(D-1, 고정 테스트) |
| Filesystem | `.HostConfig.ReadonlyRootfs`, 상태 루트 tmpfs mode | `true`, 0555. 운영 파일은 컨테이너 안에 없고, 상태 루트에는 쓸 수 없음 |
| Output | 표준 출력 | `PASS`/`FAIL` 단어, 소스 sha256, 합성 루트 경로, `RESULT`뿐. 키·digest·금액 없음 |
| Exit Code | `docker start -a`의 종료 코드 | 성공 0, 실패 0이 아님 |

**임시 루트 규칙(진입점 구현 조건):**
- 합성 루트는 `TemporaryDirectory(dir="/tmp")`로만 만든다. 만들 수 없으면 `FAIL`, exit ≠ 0으로 끝낸다.
- 기본 상태 경로(`STATE_REL`, 인자 없는 `state_dir()`)로 **fallback하지 않는다.** 고정 테스트가 이를 검사한다.
- 루트가 `/app/.runtime_governance_state` 아래로 풀리면 `FAIL`이다.

#### 2.5.1 격리 실패 조건

다음 중 하나라도 보이면 실행하지 않거나 즉시 중단한다. 상태는 VALIDATION_PATH_UNRESOLVED 그대로다.
- 실제 운영 상태 디렉터리 mount, 또는 운영 volume 연결
- 네트워크 모드가 `none`이 아님, 또는 예상하지 못한 외부 통신
- 주입된 환경 변수(특히 Secret 이름)
- root, privileged, 남은 capability
- 합성 데이터가 기본 상태 경로로 fallback
- 예상하지 못한 파일 생성
- 출력에 금융 데이터나 Secret
- 이미지 id가 승인 candidate와 다름

### 2.6 구현 (2026-10-12, Thomas 조건부 승인)

**V-1·V-2 수행.** Thomas가 정책 조건 10개를 붙여 구현을 승인했고, 진입점을 구현했다. Runtime 코드는 바꾸지 않았다.
- **파일:** `scripts/c7_blocked_selftest.py`, 실행은 `python -m scripts.c7_blocked_selftest`
- **검사 10개(§2.3의 7개를 넓힘):**
  - `synthetic_root_isolated`, `healthy_is_quiet`, `broken_is_told_once_and_stays_quiet`
  - `failed_delivery_is_offered_again`, `failed_record_is_offered_again`
  - `repair_is_cleared_once_and_recurrence_told`, `vanished_is_not_cleared`, `stale_token_is_refused`
  - `lock_is_not_blocked`, `alert_text_carries_no_key`

**D-1–D-9의 구현 근거**
- **입력(D-1, D-5):** 인자가 있으면 아무것도 돌리기 전에 `FAIL input_refused`, exit 2. 환경 변수와 stdin은 읽지 않는다(AST 테스트).
- **데이터 격리(D-2, D-3):**
  - 검사마다 새 `TemporaryDirectory`를 만든다. 모든 호출에 그 안의 경로를 명시한다.
  - `cash_flows`만 import하고 `store`는 import하지 않는다. `store.state_dir()`는 기본값이 실제 상태 경로다.
  - `state_dir(...)` 호출과 `STATE_REL`이 없다(AST 테스트).
  - 루트를 만들 수 없거나 `.runtime_governance_state` 아래로 풀리면 FAIL이다. fallback은 없다(테스트).
  - 모든 lane이 임시 루트 안에서만 열리고 끝나면 지워진다(테스트).
- **네트워크·전송(D-4):** 새 인터프리터에서 진입점만 import했을 때 `socket`, `ssl`, `http.client`, `urllib.request`, `requests`, `httpx`가 로드되지 않는다. operator, providers, scheduler, `holdings.store`도 로드되지 않는다(테스트). mock이 아니라 실제 import 그래프로 확인했다. 전송은 "기록할지"를 이 프로세스가 정하는 것으로 대체했다.
- **출력(D-6):** `PASS|FAIL <검사>`, `SOURCE <sha256> <경로>`, `RESULT PASS|FAIL`뿐이다(정규식 테스트). 예외는 메시지 없는 `FAIL`로 끝난다(테스트).
- **검토와 고정(D-7, D-8):** 이 PR의 검토 대상이다. `tests/test_c7_blocked_selftest.py` 18건이 위를 고정한다. PR-D 동작을 망가뜨리면(해소 보류 규칙 제거, 오래된 기록 거부 제거, 판정 침묵) 해당 검사가 FAIL이 된다.
- **Runtime 불변(D-9):** `runtime/`의 어떤 모듈도 진입점을 import하지 않는다(테스트).

**원장 접근 금지 테스트의 예외(Thomas 결정 2026-10-12)**
- `tests/test_mvp_runtime_holdings_cash_flows.py::test_no_door_module_reads_the_ledger`는 import 검사와 파일명 리터럴 검사를 나눠 판정하도록 바꿨다.
- 정확히 `scripts/c7_blocked_selftest.py` 하나만 `cash_flows` **import**가 허용된다(`IMPORT_ONLY_EXEMPT`). 원장·상태 파일명 리터럴 금지는 이 파일에도 그대로 적용된다.
- 기존 허용 파일 셋(`DOOR_ALLOWED`)은 그대로다.
- 부정 테스트로 다음을 고정했다:
  - 이 파일에 리터럴이 생기면 거부된다.
  - 다른 모듈의 import는 거부된다.
  - 이름만 같은 다른 경로는 거부된다.
  - 예외 목록이 늘어나지 않는다.
- 일반적인 원장 접근 권한의 확대가 아니다.

**이 구현으로 바뀌지 않는 것**
- candidate 이미지 안에서 돌리지 않았다.
- D-10 ISOLATION EVIDENCE(§2.5)는 확보하지 않았다.
- 저장소 테스트는 pytest 안에서만 진입점을 돌렸다.

그래서 상태는 VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED이고, 배포를 막는 VALIDATION_PATH_UNRESOLVED는 그대로다. 이 진입점이 candidate 묶음에 들어가므로, 배포 범위 계산(준비 문서 §2)은 최종 기준선에서 다시 한다(§3.5).

## 3. Candidate·Rollback 연결 검증

### 3.1 원칙

- 태그는 묶음의 이름이다. 코드의 증거는 아래 연결 고리다. 태그를 언제 정하는지는 §3.5에 있다(`candidate-1217` — PROVISIONAL_RECOMMENDATION, CANDIDATE_TAG_DECISION_REQUIRED).
- 운영 메타데이터(2026-10-12T06:15Z 관측: 여덟 서비스 `67d9b622d648` = `candidate-1210` = `latest`)는 관측 당시의 사실이다. 배포 때 다시 읽는다.

### 3.2 연결 고리와 단계 (배포 승인 이후의 절차, 지금 실행하지 않음)

| 단계 | 실행 주체 | 승인 | 예상 증거 | 중단 조건 |
|---|---|---|---|---|
| C-1 승인 SHA 고정 | Thomas | 배포 승인의 일부 | 배포할 origin/main 전체 SHA X | X가 #1216·#1217 병합 커밋을 포함하지 않으면 중단 |
| C-2 깨끗한 worktree | 배포자 | 배포 승인 | `/root/deploy-<N>`의 `HEAD` = X, `git status` 비어 있음 | 다르면 중단 |
| C-3 첫 preflight | 배포자 | 배포 승인 | `deploy_preflight.py <N>`에 STOP 없음, `rollback` 줄이 태그할 원본을 지목 | STOP이면 worktree 제거 후 보고 |
| C-4 배포 전 이미지 기록 | 배포자 | 배포 승인(메타데이터 읽기) | 여덟 컨테이너의 `.Image`가 모두 같은 id P(지금 관측은 `67d9b622d648`) | 서로 다르면 중단 |
| C-5 rollback 태그 | 배포자 | 배포 승인 | `rollback-pre-<N>` 생성 뒤 `docker image inspect --format '{{.Id}}'` = P | ≠ P면 태그를 지우고 중단 |
| C-6 candidate 빌드 | 배포자 | 배포 승인 | `candidate-<N>`(§3.5에서 확정한 번호) 이미지 id Q, `latest`는 여전히 P | `latest`가 바뀌면 중단 |
| C-7 코드 동일성 | 배포자 | **필요**(컨테이너 실행) | candidate 안 `runtime/`·`scripts/` 파일의 sha256 목록이 worktree X의 같은 목록과 같다. 기준 목록은 목록과 해시를 **같은 디렉터리에서** 만든다: `(cd /root/deploy-<N> && git ls-files -z runtime scripts \| sort -z \| xargs -0 sha256sum)`. 이전 문안 `git -C … ls-files \| xargs -0 sha256sum`은 해시를 호출한 위치에서 계산해 틀린다. 이미지 쪽은 `--entrypoint sh`로 `cd /app && find runtime scripts -type f \| sort \| xargs sha256sum`만 돌리는 격리 실행(D-10 플래그)이다. 두 목록을 경로 기준으로 정렬해 비교한다. **`python`도 `-m`도 holdings import도 상태 mount도 없다.** 파일 바이트를 읽을 뿐이고 코드를 실행하지 않는다. 그래서 §2.1의 5단계 방식과 다르다. 진입점이 승인되면 V-4의 소스 해시가 이를 보강한다 | 한 줄이라도 다르면 candidate 폐기, 중단 |
| C-8 동작 검증 | 2.4 V-4 | **필요** | `RESULT PASS` | 경로 미승인이면 여기서 HOLD |
| C-9 promote 전 preflight | 배포자 | 배포 승인 | `--promote --tree`에 STOP 없음. `rollback-pre-<N>` = 지금 도는 이미지, `fires` 없음 | `promote`·`fires` STOP이면 대기 또는 중단 |
| C-10 promote·up | 배포자 | **배포 승인** | 여덟 컨테이너 `.Image` = Q, healthy, 두 lane의 다음 `fired` | 하나라도 아니면 3.3의 복구 |

### 3.3 Rollback 검증 수준 — 세 상태

각 수준이 **무엇을 증명하고 무엇을 증명하지 않는지**를 나눈다. 아래 명령과 메타데이터 읽기는 배포 승인 이후의 일이고, 지금은 실행하지 않는다.

| 상태 | 증거 | 증명하지 않는 것 | 권고 |
|---|---|---|---|
| **ROLLBACK_TARGET_VERIFIED** | C-4·C-5·C-9: 배포 전 여덟 서비스의 이미지 id P, `rollback-pre-<N>` 존재, 그 id = P, 이미지가 저장소에 있음(`docker image inspect` 성공) | 복구가 실제로 성공한다는 것 | **권고** |
| **ROLLBACK_PROCEDURE_REVIEWED** | 다음 항목을 문서로 확인한다. 실행은 하지 않는다.<br>- 스킬의 복구 명령(`docker tag …rollback-pre-<N> …latest` + 깨끗한 tree의 `compose up -d`)<br>- compose 프로젝트(`thomas_agent`), 기준 compose 파일, env 파일 경로(`/root/thomas_agent/.env`, 값은 읽지 않음)<br>- 재생성 범위(여덟 서비스), fire 충돌 조건(preflight `fires`), 복구 뒤 health·`fired` 확인 방법, 복구 실패 시 대응 | 명령이 실제로 성공한다는 것 | **권고**(TARGET과 함께) |
| **ROLLBACK_FUNCTIONAL_VERIFIED** | 별도 승인된 실제 복구 시험이 필요하다. 명령 성공, 대상 이미지 복원, 여덟 서비스 재시작과 healthy, scheduler의 다음 `fired`, 오류 기록 | — | **권하지 않는다.** 운영을 두 번 재생성해 fire를 끊을 위험이 있다. 실패 시의 실제 복구에서만 생긴다 |

- **Gate 6:** Thomas는 나중에 TARGET + PROCEDURE를 Gate 6 충족으로 승인할 수 있다. 그래도 세 상태는 따로 기록한다. **TARGET + PROCEDURE를 ROLLBACK_FUNCTIONAL_VERIFIED로 적지 않는다.** 결정 전까지 상태는 ROLLBACK_NOT_VERIFIED다.
- **Rollback 안전성:** Rollback은 Runtime 이미지 되돌림이다. 금융 데이터 복구가 아니다. 다음은 바꾸지 않는다: 금융 원장, holdings 상태 파일, cutover, 체크포인트, backfill, 낙폭 기준선, H6c, 거래 단계, 자동 리밸런싱.

### 3.4 여덟 서비스 영향 (준비 문서 §3.1–§3.2 요약과 보강)

- `up -d`는 여덟 서비스를 모두 다시 만든다. preflight가 보는 진행 중 작업은 scheduler lane의 fire뿐이다.
- operator의 대화, pipeline-worker의 작업, dispatch-bridge의 workflow는 운영자가 확인한다(OPERATOR_EVIDENCE_REQUIRED).
- **피할 시간대:**
  - factory fire 08:09–08:52 UTC(preflight가 막는다)
  - holdings fire 직전·직후. PR-D가 바꾸는 lane이라, 배포 직후 첫 holdings fire를 관찰할 수 있게 그 사이에 둔다.
- **실패 시:** 서비스별로 고치지 않는다. 이미지 하나를 되돌려 한 번의 `up -d`로 여덟 서비스를 함께 되돌린다.

### 3.5 Candidate 태그를 정하는 시점

**Candidate 태그는 최종 배포 기준선과 거기 들어갈 구현 PR이 정해진 뒤에 정한다.**

- 지금의 `candidate-1217`은 **PROVISIONAL_RECOMMENDATION**이다. 상태는 CANDIDATE_TAG_DECISION_REQUIRED다.
- 자가검증 진입점 구현 PR(V-2)이 병합되면 배포 묶음의 마지막 PR이 바뀐다. 그러면 관례상 번호도 바뀔 수 있다.

**확정 전 확인 사항**
1. PR-D 구현 PR(#1216)
2. Guard 보완 PR(#1217)
3. 자가검증 진입점 구현 PR이 있는지, 그리고 병합됐는지
4. 최종 origin/main SHA
5. 배포 스킬의 `<N>`(PR 번호) 계약
6. `deploy_preflight.py`의 PR 검증. `<N>`이 병합됐고 그 병합 커밋이 origin/main에 있어야 한다
7. 태그 충돌. `candidate-<N>`·`rollback-pre-<N>`이 이미 있으면 다른 세션이 배포 중이라는 preflight STOP이다
8. 기존 candidate 태그 보존(지우지 않음)
9. rollback 태그와의 관계(같은 `<N>`)

**식별 증거는 따로 기록한다**

| 항목 | 뜻 |
|---|---|
| Approved Git SHA | 승인된 소스 기준(C-1) |
| Build Git SHA | 실제로 빌드한 worktree의 `HEAD`(C-2) |
| Candidate Tag | 배포 묶음의 이름 |
| Candidate Image ID | 이미지 식별자(C-6의 Q) |
| Source Hashes | 이미지 안 코드 동일성 증거(C-7, V-4) |
| Rollback Image ID | 복구 대상(C-4의 P) |

태그만으로 코드 동일성을 증명하지 않는다.

**배포 범위는 다시 계산한다.** 준비 문서의 "이미지에 들어가는 변화는 네 파일"은 최종 배포 시점의 확정값이 아니다. 구현 PR이 더해지면 다음을 다시 계산한다: 변경 파일, 이미지에 들어가는 코드, Runtime 동작 영향, CI 증거, 이미지 검증 대상, 서비스 영향, Rollback 호환성.

## 4. 남은 배포 차단 조건과 결정

| 조건 | 지금 | 풀리는 조건 |
|---|---|---|
| Gate 2 직접 합성 | **GATE_2_FULLY_SATISFIED**(GATE_2_DIRECT_SYNTHETIC_TEST_PASSED, Thomas 수용 2026-10-12). 새 세션 실탐침은 NEW_SESSION_TOLD_PROBE_NOT_PERFORMED | 해소 |
| 이미지 내부 동작 | VALIDATION_PATH_UNRESOLVED. V-1(정책)과 V-2(구현)는 됐다(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED, §2.6) | V-3 병합, V-4 candidate 격리 실행(D-10 ISOLATION EVIDENCE), V-5 수용 |
| Candidate 태그 | CANDIDATE_TAG_DECISION_REQUIRED (`candidate-1217` — PROVISIONAL_RECOMMENDATION) | 최종 배포 기준선 확정 뒤 Thomas 확정(§3.5) |
| Rollback | ROLLBACK_NOT_VERIFIED | §3.3 수준 결정 + 배포 시점 C-4·C-5·C-9(TARGET), 절차 검토(PROCEDURE). FUNCTIONAL은 별도 |
| 여덟 서비스 진행 작업 | OPERATOR_EVIDENCE_REQUIRED | 배포 시점 운영자 확인 |
| 배포 승인 | NOT_GRANTED | Thomas 승인 |
| 판정 | **DEPLOYMENT_HOLD** | 위 모두 |

**Gate 2의 Fail-Closed 해석.** 준비 문서는 "Gate 2가 PENDING 또는 FAILED면 중단"이라고 적는다. 작성 당시 Gate 2는 부분 충족이었고, 그 중간 상태도 막힘으로 읽었다(2026-10-12 수용 뒤로는 충족):

`GATE_2_NOT_FULLY_SATISFIED → DEPLOYMENT_HOLD`

새 권한을 만드는 규칙이 아니라, 기존 필수 조건을 Fail-Closed로 해석한 것이다. 이 표현을 준비 문서에 반영하는 일은 후속 문서 작업으로 남긴다(이번 PR에서 준비 문서는 바꾸지 않는다).

**Thomas 결정 항목**

1. ~~Gate 2: 가안 승인 여부~~ — 해소(kit 변경·실행 승인, 결과 수용 2026-10-12. 새 세션 탐침은 필수 아님)
2. 이미지 검증: V-1·V-2는 됐다(조건부 승인, 구현 PR). 남은 것은 구현 PR 병합(V-3), candidate 격리 실행(V-4)
3. Rollback: ROLLBACK_TARGET_VERIFIED + ROLLBACK_PROCEDURE_REVIEWED를 Gate 6 충족으로 볼지(FUNCTIONAL과는 따로 기록)
4. Candidate 태그 확정(최종 배포 기준선이 정해진 뒤, §3.5)
5. D-10 ISOLATION EVIDENCE(§2.5)를 V-4의 필수 증거로 받아들일지
6. 배포 승인(위가 모두 풀린 뒤)

이 문서의 병합이나 CI 통과는 어떤 상태도 바꾸지 않는다. Gate 2는 2026-10-12 Thomas의 수용으로 충족됐다. 그 사실은 배포 승인이 아니다.

## 5. 하지 않은 것

Guard 탐침, Global Guard kit 변경·실행, Guard 규칙 변경·우회, `docker run`/`exec`, 이미지 빌드·태그·배포, 운영 컨테이너 변경, 실제 금융 데이터·Secret 접근, 실제 Telegram 발송, Runtime 코드 구현, PR-A·B·C.
