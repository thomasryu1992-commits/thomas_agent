# 제안: C7 PR-D 운영 배포 준비 패킷 — 무엇이 실리고, 무엇을 확인하고, 어떻게 되돌리나 (DRAFT v0.1)

**상태:** DRAFT 2026-10-12 — C7 PR-D(#1216)를 운영에 올리기 전의 준비 패킷이다. 문서 준비 판정은 PREPARATION_COMPLETE_PENDING_EVIDENCE이고, 운영 배포 판정은 따로 DEPLOYMENT_HOLD다(§10). 배포를 막는 것은 Global Guard 반영 증거(GLOBAL_GUARD_UPDATE_PENDING), 현재 운영 이미지(UNKNOWN), Rollback(ROLLBACK_NOT_VERIFIED), Guard를 지키는 이미지 내 동작 검증 경로(DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED), 배포 승인(NOT_GRANTED)이다. DECISION_REQUIRED가 둘 있다: candidate 태그(CANDIDATE_TAG_DECISION_REQUIRED, 기본 권고 `candidate-1217`)와 이미지 내 동작 검증 경로(SEPARATE_APPROVAL_REQUIRED, §4.3).

**기준:** origin/main `1f328ffd`(#1217), 2026-10-12. 코드와 문서만 읽었다. 운영 컨테이너, 이미지, 상태 파일, 원장, 거래소 API에는 이번 작업에서 접근하지 않았다. 운영 상태 값은 이전 작업의 관측이고, 그렇게 표시했다.
**소유:** 배포 절차의 권위는 `.claude/skills/deploy/SKILL.md`와 `scripts/ops/deploy_preflight.py`, PR-D 설계의 권위는 `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §6이다. 이 패킷은 둘을 바꾸지 않는다.

---

## 0. 한 줄

- **실리는 것은 origin/main 전체다.** 이미지에 들어가는 변화는 #1210 이후 네 파일뿐이다(§2).
- **코드 동작이 바뀌는 곳은 holdings fire 하나지만, 배포가 건드리는 것은 여덟 서비스다(§3).** 장애가 없으면 새 메시지도, 새 파일도, 새 status 단어도 없다.
- **문서 준비와 배포 허가는 다르다.** 문서는 PREPARATION_COMPLETE_PENDING_EVIDENCE이고, 배포는 DEPLOYMENT_HOLD다. 증거 셋(Global Guard, 지금 도는 이미지, Rollback)과 승인된 이미지 내 동작 검증 경로가 없다. 안전성 불변식이 코드에서 의심스러운 곳은 없다. HOLD는 그 때문이 아니라 위의 빈칸 때문이다.
- **CI 통과와 이 문서의 병합은 배포 권한을 주지 않는다.**

## 1. 기준선

| 항목 | 값 | 근거 |
|---|---|---|
| 승인된 코드 | #1216 → `52b744e9` | MERGED 2026-10-12T03:17Z |
| 보안 보완 | #1217 → `1f328ffd` | MERGED 2026-10-12T03:59Z |
| 현재 main | `1f328ffd357f1cd7f8c304e9c882ad634ef6f92a` | 필수 CI 5개·Safety invariants 성공 |
| 현재 운영 이미지 | **UNKNOWN** | 마지막 관측(2026-10-12T03:17Z, 읽기 전용): scheduler·scheduler-maint = `67d9b622d648` = `candidate-1210` = `latest`. 이번 작업에서는 다시 보지 않았다 |
| 현재 운영 candidate | **UNKNOWN** | 같은 관측에서 `candidate-1210`. 이미지에는 git SHA 라벨이 없다(Dockerfile에 `LABEL` 없음) |
| 신규 candidate | NOT_BUILT | |
| Rollback 이미지 | **ROLLBACK_NOT_VERIFIED** | 같은 관측에서 `rollback-pre-1210` = `cb57fab0baba`(1206 이미지)였다. 이번 배포의 rollback 점은 배포 시점에 preflight가 정한다 |
| Global Guard | **GLOBAL_GUARD_UPDATE_PENDING** | 설치본 sha256 `865e9eec…`(mtime 2026-10-11 17:03Z), main의 Guard `88addc9d…`. 아직 반영되지 않았다(§5 Gate 2) |
| Candidate 태그 | **CANDIDATE_TAG_DECISION_REQUIRED** | 기본 권고 `candidate-1217`(§4.1) |
| 이미지 내 동작 검증 | **SEPARATE_APPROVAL_REQUIRED** | Guard를 지키는 경로가 승인되지 않았다(§4.3) |
| 배포 승인 | NOT_GRANTED | |

## 2. 배포 대상과 변경 범위

배포는 PR 하나가 아니라 **origin/main 전체**를 싣는다(CLAUDE.md, Deploying). 마지막으로 배포된 #1210(`b6d4bf30`) 이후 main의 커밋은 8개이고, 이미지에 복사되는 경로(`runtime/`, `scripts/`, `schemas/`, `governance/`, `03_ROLE_CONTRACTS/`, `05_REGISTRIES/`, `THOMAS_CORE/`, `requirements-runtime.txt`)의 차이는 네 파일이다.

| 파일 | 출처 | 운영에서의 도달 |
|---|---|---|
| `runtime/mvp_runtime/holdings/cash_flows.py` | #1212 `toss_continuity*`, #1216 BLOCKED 판정·told 기록 | BLOCKED: holdings fire마다. toss_continuity: 아래 터미널 모드에서만 |
| `runtime/mvp_runtime/holdings/store.py` | #1216 `blocked_check`, `mark_blocked_told`, `_reason` | holdings fire |
| `runtime/mvp_runtime/scheduler.py` | #1216 네 번째 알림 묶음 | holdings fire |
| `scripts/holdings_board.py` | #1212 `--toss-continuity` | 터미널 전용. Guard가 Claude 세션에서 거부한다(설치본도 이 모드를 안다) |

- #1217(`.claude/`, `tests/`)과 문서 PR(#1209, #1211, #1213–#1215)은 이미지에 들어가지 않는다.
- 이 기준은 "운영 이미지가 `b6d4bf30`에서 빌드됐다"는 가정 위에 있다. 이미지에 SHA 라벨이 없으므로, 배포 때 §4.3의 확인이 이 가정을 대신한다.
- `docker-compose.yml`, `Dockerfile`, 환경 변수는 바뀌지 않았다. 새 env, 새 mount, 새 서비스는 없다.

**바뀌지 않는 것:** 거래 실행 권한, 원장 기록 정책과 형식, cutover, H6b/H6c, 자동 리밸런싱, 낙폭 기준선(`baseline_baa665…`), Trading Stage(PAPER), 거래소 API 권한. PR-D는 알림이고, 복구가 아니다.

**PR-D가 보장하지 않는 것:** 체크포인트 기반 복원 무결성(PR-A), 원장 이력 완전성(PR-B), 쓰기 차단과 재개(PR-C), exactly-once 전송(at-least-once다), 자동 원장 복원.

## 3. 서비스별 영향

두 가지를 구분한다.

- **A. 코드 동작 변화:** 바뀌는 곳은 maintenance lane의 holdings fire다. 그 fire가 쓰는 바깥 표의 값 하나(`cash_flows.error`)를 operator와 read-bridge가 보여 준다.
- **B. 배포 영향 범위:** promote 뒤 `up -d`는 `thomas-agent-runtime` 이미지를 쓰는 **여덟 서비스를 모두 다시 만든다**(operator, scheduler, scheduler-maint, read-bridge, dispatch-bridge, pipeline-worker, knowledge-bridge, switch-bridge).
  - 그래서 바뀐 코드는 holdings뿐이어도 배포 위험은 holdings에 그치지 않는다. 재생성 순간에 risk lane의 fire, operator의 대화, pipeline-worker의 작업, 각 bridge의 요청이 끊길 수 있다.
  - hermes는 별도 이미지라 다시 만들어지지 않는다.

| 서비스 | 다시 만들어짐 | 동작 변화 | 근거 | Rollback |
|---|---|---|---|---|
| `scheduler-maint` | 예 | **있음**: holdings fire 끝에 BLOCKED 판정(읽기 전용) 한 번, 필요할 때 Telegram 1건과 told 기록 | `holdings_refresh` ∈ `MAINTENANCE_KINDS` | 이미지 되돌림 |
| `scheduler` (risk lane) | 예 | 없음. holdings kind를 돌지 않는다 | `lane_kinds("risk")` | 이미지 되돌림 |
| `operator`, `read-bridge` | 예 | 보이는 값 하나: `holdings` 보기의 `cash_flows.error`가 reason_code가 된다 | `domain_console._holdings_status` → `store.load_holdings_view`가 바깥 표를 읽는다. 그 함수는 바뀌지 않았고, 표에 쓰인 값이 바뀐다 | 이미지 되돌림(되돌린 뒤 다음 fire부터 옛 값) |
| `dispatch-bridge`, `pipeline-worker`, `knowledge-bridge`, `switch-bridge` | 예 | 없음 | 변경 파일의 바뀐 함수를 부르지 않는다 | 이미지 되돌림 |
| `hermes` | 아니오 | 없음 | 별도 이미지(`hermes-agent`), 배포가 건드리지 않는다 | 해당 없음 |

- **Telegram:** 기존 holdings 알림(낙폭, readiness, 예외)과 같은 `_tell` → `operator_mod.notify_operator` 경로와 같은 env(`MVP_OPERATOR_CHANNEL`, `TELEGRAM_BOT_TOKEN`, scheduler-maint)를 쓴다. 새 채널은 없다.
- **보드:** 바깥으로 나가는 한 표(`holdings_snapshot.json`)의 `cash_flows.error`가 예외 클래스 이름(`ToolError`) 대신 reason_code를 담는다. 금액은 들어가지 않는다.
- **원장 접근:** fire마다 원장 사슬과 상태 파일을 읽기 전용으로 한 번 더 읽는다(`blocked_state`). 운영 원장의 크기와 그 비용은 이번 작업에서 재지 않았다(OPERATOR_EVIDENCE_REQUIRED).
- **told 파일:** `holdings/holdings_cash_flow_blocked_told.json`. 배포 때 만들어지지 않는다. 알림이 전달된 뒤에만 uid 10001이 쓴다. 기존 상태 잠금 sidecar(`holdings_cash_flow_state.lock`) 안에서 쓴다.
- **첫 fire:** 지금 운영 원장이 이미 거부 상태라면, 배포 뒤 첫 holdings fire가 BLOCKED Telegram을 바로 보낸다. 그것이 이 기능이고, 회귀가 아니다. 지금 원장 상태는 OPERATOR_EVIDENCE_REQUIRED다(한 표의 `cash_flows.error`로 보인다. 이번 작업에서는 읽지 않았다).

### 3.1 여덟 서비스 재생성: 배포 전 확인 계획 (실행하지 않음)

모든 항목의 분류는 OPERATOR_EVIDENCE_REQUIRED다. 운영 Docker 상태는 이번 작업에서 읽지 않았다.

| 확인 | 방법 |
|---|---|
| 여덟 서비스의 지금 이미지 id | 서비스마다 `docker inspect <container> --format '{{.Image}}'`. 모두 같은 id인지, 그 id에 붙은 태그가 무엇인지 확인한다 |
| 실제 재생성 대상 | `docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f /root/deploy-<N>/docker-compose.yml config --services`(서비스 이름만 출력한다. 전체 `config` 출력은 `.env`의 Secret을 담으므로 출력하지 않는다). preflight도 compose 라벨과 bind source를 읽는다 |
| 서비스별 rollback 가능성 | 여덟 서비스가 한 이미지를 쓰므로 rollback 태그 하나가 모두를 되돌린다. 그 태그가 지금 여덟 서비스의 이미지 id와 같아야 한다 |
| 진행 중인 fire | preflight `fires` 검사. 두 lane의 `started`에 짝이 없는 이벤트가 있거나 10분 안에 factory fire가 있으면 STOP |
| 재생성 중 끊김 | operator·pipeline-worker·bridge에 진행 중인 작업이 있는지 운영자가 확인한다. 지금 preflight가 보는 것은 scheduler lane의 fire뿐이다 |
| 서비스별 health | `docker ps`의 health 상태. healthcheck가 있는 서비스는 healthy, 없는 서비스는 running과 재시작 횟수 |
| 배포 후 이미지 일치 | 여덟 서비스의 `{{.Image}}`가 모두 candidate id |
| 원래 이미지 = rollback 태그 | promote 직전 `--promote` preflight가 확인한다(`rollback-pre-<N>`이 지금 도는 이미지) |

- **복구 순서:** 실패하면 서비스별로 고치지 않고 이미지 하나를 되돌린다(§8). 한 번의 `up -d`로 여덟 서비스가 함께 돌아간다. 그 뒤 health를 확인하고, scheduler 두 lane의 다음 `fired`를 기다린다.

## 4. Candidate 이미지 계획 (실행하지 않음)

### 4.1 이름과 기준

- **태그:** `thomas-agent-runtime:candidate-<N>`, rollback 점은 `rollback-pre-<N>`.
- **`<N>` (CANDIDATE_TAG_DECISION_REQUIRED):** 기본 권고는 **`candidate-1217`**이고, 아직 승인된 태그가 아니다.
  - 근거:
    - 선례는 묶음의 마지막 PR이다(#1204+#1206 → `candidate-1206`, #1208+#1210 → `candidate-1210`).
    - #1217은 이번 묶음의 마지막 보안 PR이다.
    - 이 태그 하나로 PR-D와 Guard 보완이라는 배포 준비 단위를 가리킨다.
  - 대안은 `candidate-1216`이다. #1217은 이미지에 들어가지 않는다.
  - `deploy_preflight.py`는 `<N>`이 병합됐고 그 병합 커밋이 origin/main에 있는지만 보므로, 둘 다 통과한다.
- **태그는 배포 묶음의 이름이지 코드 버전의 증거가 아니다.** 실제 배포의 코드 식별은 다음 다섯 가지로 한다. 배포 보고에 모두 적는다.
  - 승인된 git SHA
  - 빌드 기준 git SHA(빌드한 worktree의 `HEAD`)
  - candidate 이미지 id
  - 빌드 뒤 이미지 내용 검증(§4.3의 CODE_IDENTITY_VERIFICATION)
  - rollback 이미지 id
- **기준 SHA:** 배포 시점의 origin/main. 그 사이 main이 움직이면 실리는 범위(§2)를 다시 계산한다.
- **빌드 시점:** Thomas의 배포 승인 뒤, Gate 1–6을 통과한 다음. 스킬 1–4단계 순서를 따른다(깨끗한 `/root/deploy-<N>` worktree → preflight → rollback 태그 → `docker build -t …:candidate-<N>`). `latest`로 빌드하지 않고, `compose up --build`를 쓰지 않는다.

### 4.2 기존 이미지 보존

- rollback 태그는 **지금 도는 이미지**에 붙인다. preflight의 `rollback` 줄이 무엇에서 태그할지 정한다(`latest`, 또는 다른 세션이 배포 없이 빌드했다면 이전 `rollback-pre-*`). raw 이미지 id에서 태그하지 않는다.
- promote 직전 `deploy_preflight.py <N> --promote --tree /root/deploy-<N>`를 다시 돌린다. 여기서 `promote` STOP은 다른 세션의 재배포를, `fires` STOP은 진행 중인 fire를 뜻한다.

### 4.3 이미지 안에서의 확인 — DEPLOY_VALIDATION_PATH_UNRESOLVED

**문제.**
- 스킬 5단계는 `docker run --rm --entrypoint python thomas-agent-runtime:candidate-<N> -c "…"`로 바뀐 동작을 **호출해서** 확인하라고 한다.
- PR-D의 동작은 `runtime.mvp_runtime.holdings` 안에 있다.
- 그런데 LOCAL_ONLY Guard는 holdings 패키지를 import하는 인라인 Python을 거부한다.
- 그래서 스킬이 적은 방식으로는 이 확인을 할 수 없다. 이 상태를 DEPLOY_VALIDATION_PATH_UNRESOLVED로 분류한다.

**원칙.** Guard가 막은 작업을 다른 명령, 다른 실행기, 다른 실행 주체로 옮긴다고 해서 자동으로 허용되지는 않는다. Thomas가 직접 하는 작업도 기존 Guard 정책과 운영 승인 경계를 따른다. 별도의 정식 승인 없이 다음을 하지 않고, 실행 가능한 절차로 제시하지도 않는다.

- Python 모듈 이름을 실행 중에 조합한 import
- 다른 Python 실행기를 통한 Guard 회피
- Docker 명령을 이용한 차단 경로 우회
- Guard 비활성화
- Global Guard 설정 변경
- 실제 금융 데이터가 연결된 컨테이너에서의 합성 검증

**검증은 세 종류로 나눈다.**

| 분류 | 목적 | 대상과 조건 | 현재 |
|---|---|---|---|
| **CODE_IDENTITY_VERIFICATION** | candidate 이미지에 승인된 코드가 들어갔는지 | git SHA, 이미지 id, 바뀐 네 파일(§2)의 sha256과 기준 SHA의 git blob 비교, 빌드 기준선, 변경 범위 | 계획 수립. 배포 승인 뒤 빌드와 함께 수행할 수 있다. **코드가 같다는 사실은 Runtime 동작을 검증하지 않는다** |
| **SYNTHETIC_RUNTIME_VALIDATION** | 실제 금융 데이터 없이 BLOCKED 감지와 알림 상태 전이를 확인 | 명시적으로 승인된 경로, Guard 준수, 실제 금융 데이터·운영 상태 디렉터리·계좌 API·Secret 미사용, 실제 Telegram 전송 없음 | **SEPARATE_APPROVAL_REQUIRED**. 승인된 경로가 없다. 저장소 CI의 합성 테스트 47건이 같은 상태 전이를 덮지만, 그것은 이미지가 아니라 소스에 대한 검증이다 |
| **PRODUCTION_RUNTIME_VERIFICATION** | 운영 환경에서의 정상 동작 | 실제 원장에 닿는 확인은 자동으로 하지 않는다 | **SEPARATE_APPROVAL_REQUIRED** |

**배포 차단 조건.** 승인된 이미지 내부 동작 검증 경로가 없으면 운영 배포를 진행하지 않는다.
- 상태: **DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED**
- 이것은 배포 실행에 대한 차단이다. 문서 준비 판정(PREPARATION_COMPLETE_PENDING_EVIDENCE)과는 별개다.
- 이 패킷은 검증 경로를 구현하지 않고, Guard 정책을 바꾸지 않는다.
- 경로를 정하는 일은 Thomas의 결정이다. 예를 들어 이미지에 포함된 정식 진입점을 새로 만드는 코드 변경이 있는데, 이것도 별도 승인 대상이다.

## 5. 배포 전 Gate

| Gate | 조건 | 현재 |
|---|---|---|
| 1 코드 무결성 | #1216·#1217이 main에 있음, 필수 CI 5개 PASS, Safety invariants PASS, Release Gate PASS, candidate가 기준 SHA로 연결됨 | main과 CI는 **충족**. candidate 연결은 빌드 때 CODE_IDENTITY_VERIFICATION(§4.3)으로 확인 |
| 2 Global Guard | 설치본 sha = main의 Guard sha, kit `check` exit 0, kit `test_guard.py` ALL PASS, told 파일명 합성 DENY, `holdings_snapshot.json` 합성 ALLOW, 새 세션 적용 확인, `.prev` 백업 존재 | **GLOBAL_GUARD_UPDATE_PENDING**. 설치본 `865e9eec…` ≠ main `88addc9d…`. 나머지 증거 없음 |
| 3 LOCAL_ONLY | 준비 확인이 실제 금융 데이터 없이 가능, Secret 노출 없음, 새 외부 출력 없음, 자동 원장 복구 없음 | **충족(코드 기준)**. 새 외부 출력은 Telegram 문구 하나이고 키·금액·계정이 없다(테스트 고정). told 파일은 상태 디렉터리 안 |
| 4 Runtime 호환성 | 원장·상태 형식 변화 없음, told 파일 초기 생성 계약, 잠금 계약, 기존 scheduler 동작 유지 | **충족(코드와 합성 테스트 기준)**. PR-D는 told 파일만 쓴다. 잠금은 기존 `_state_lock`이다. 판정은 잠금을 잡지 않는다 |
| 5 운영자 알림(소스) | 최초 BLOCKED, 지속 중 중복 없음, 전송 실패 시 재알림, 기록 실패 시 재알림, 해소 1회, 재발 재알림, 파일 소실 시 잘못된 해소 없음, 금융 데이터 비노출 | **충족(합성)**. `tests/test_mvp_runtime_holdings_c7_blocked.py` 47건이 각 항목을 덮는다. 실제 채널 확인은 SEPARATE_APPROVAL_REQUIRED |
| 5b 이미지 내 동작 검증 경로 | Guard를 지키고 실제 데이터가 없는 SYNTHETIC_RUNTIME_VALIDATION 경로가 승인됨 | **DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED**. 승인된 경로가 없다(§4.3) |
| 6 Rollback과 서비스 재생성 | 운영 이미지 식별, rollback 이미지 존재, 명령 검토, 형식 호환, 비가역 변경 없음, 사후 확인 방법. 여기에 §3.1의 항목: 여덟 서비스의 기존 이미지 id, 실제 재생성 대상, 진행 중인 fire와 작업, 서비스별 health 방법, 원래 이미지와 rollback 태그의 일치, 배포 뒤 여덟 서비스의 이미지 id 일치 | **ROLLBACK_NOT_VERIFIED**. 명령·호환성·비가역성은 §8에 검토했다. 이미지와 서비스 항목은 OPERATOR_EVIDENCE_REQUIRED이고, 배포 시점 preflight와 운영자가 확인한다 |
| 7 운영자 승인 | Thomas의 별도 배포 승인 | **NOT_GRANTED**. 병합, CI PASS, Guard VERIFIED는 배포 승인이 아니다 |

**Gate 2가 배포 앞에 오는 이유:** 배포 뒤 told 파일이 실제로 생긴다. 상태 디렉터리 안에서는 설치본의 경로 규칙이 이미 막는다. 그러나 이름만 쓴 참조나 다른 경로의 사본은 새 규칙이 있어야 막힌다(#1217).

## 6. 배포 중단 조건

다음 중 하나면 그 자리에서 멈추고 보고한다. 우회하지 않는다.

- preflight의 어떤 STOP. 특히 `promote`(다른 세션의 재배포)와 `fires`(진행 중인 fire, 또는 10분 안의 factory fire: 매일 08:09–08:52 UTC)
- CODE_IDENTITY_VERIFICATION(§4.3)에서 불일치
- 승인된 이미지 내 동작 검증 경로가 없음: **DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED**
- 여덟 서비스의 지금 이미지 id가 서로 다르거나, rollback 태그가 그 이미지를 가리키지 않음
- operator·pipeline-worker·bridge에 끊으면 안 되는 작업이 진행 중임(운영자 판단)
- Gate 2가 PENDING 또는 FAILED
- 배포 전 운영 원장이 거부 상태라는 증거가 있는데, 그 알림을 받을 준비가 안 됨(Thomas 판단)
- 실제 금융 데이터를 읽어야만 할 수 있는 확인이 필요해짐(별도 승인)
- Guard가 배포 명령을 거부함

## 7. 배포 후 확인 계획 (실행하지 않음)

| 확인 | 방법 | 분류 |
|---|---|---|
| 도는 이미지 = candidate | 여덟 서비스 모두 `docker inspect <container> --format '{{.Image}}'`가 candidate id와 같음 | METADATA_ONLY |
| 컨테이너 정상 | `docker ps`에서 여덟 서비스 healthy | METADATA_ONLY |
| holdings fire가 돈다 | 배포 시각 뒤에 holdings 스케줄의 `fired` 이벤트(분 문자열이 아니라 이벤트 조건으로 기다림) | METADATA_ONLY |
| 건강한 lane에서 새 동작이 조용함 | 그 fire의 `last_status`에 `h6 blocked` 단어가 **없음** | METADATA_ONLY |
| 원장 형식과 정책 불변 | cutover, 체크포인트, H6c, 낙폭 기준선이 그대로임 | OPERATOR_EVIDENCE_REQUIRED(한 표와 Thomas 터미널) |
| 실제 BLOCKED 경로 | 운영에서 일부러 장애를 만들지 않는다. 합성으로 이미 덮었다 | SEPARATE_APPROVAL_REQUIRED |

**건강한 lane에는 켜졌다는 신호가 없다.** 장애가 없으면 `blocked_check`는 status 단어도 파일도 만들지 않는다. "PR-D가 돌았다"는 운영에서 직접 보이지 않는다. 말할 수 있는 것은 CODE_IDENTITY_VERIFICATION(코드가 들어감)과 fire의 정상 완료뿐이다. 둘 다 Runtime 동작의 검증은 아니다. 운영 동작 확인은 PRODUCTION_RUNTIME_VERIFICATION이고, 별도 승인 대상이다.

## 8. Rollback

- **범위:** Rollback은 Runtime 이미지를 이전 것으로 되돌리는 절차다. 금융 원장과 상태 파일을 복원하는 절차가 아니다.
- **절차(스킬):** `docker tag thomas-agent-runtime:rollback-pre-<N> thomas-agent-runtime:latest` 뒤, 깨끗한 origin/main tree에서 `docker compose -p thomas_agent --env-file /root/thomas_agent/.env -f /root/deploy-<N>/docker-compose.yml up -d`. rollback 태그가 없으면 이전 커밋을 candidate로 다시 빌드해 promote한다.
- **서비스 복구 순서:** 여덟 서비스는 한 이미지를 쓰므로 한 번의 `up -d`로 함께 돌아간다. 서비스별로 따로 되돌리지 않는다. 그 뒤 health를 확인하고, scheduler 두 lane의 다음 `fired`를 기다린다.
- **형식 호환:** PR-D는 원장과 상태 파일의 형식을 바꾸지 않는다. 쓰는 것은 told 파일 하나다.
- **남는 told 파일:** 옛 코드는 그 파일을 모르고 읽지 않는다. 지우지 않는다. 다시 배포하면 그 기록에서 이어진다. 그 사이 원장이 고쳐졌으면 해소 1회가, 원장이 새로 시작됐으면 `clearing withheld`가 나온다.
- **비가역 변경:** 없다. 다만 이미 보낸 Telegram 메시지는 되돌릴 수 없다.
- **사후 확인:** 여덟 서비스의 이미지 = rollback 태그의 id = 배포 전에 기록한 원래 이미지 id, 컨테이너 healthy, 다음 holdings fire의 `fired`.
- **Rollback 계획에서도 하지 않는 것:** 원장 삭제, 상태 파일 초기화, cutover 변경, 자동 backfill, 원장 자동 복구, 계좌 데이터 변경, H6c 활성화, 낙폭 기준선 변경(`--reset-peak` 포함).
- **판정:** 명령과 호환성은 검토했다. 그러나 rollback 태그가 실제로 지금 도는 이미지를 가리키는지, 서비스별 복구가 되는지는 배포 시점에만 확인된다. 그래서 **ROLLBACK_NOT_VERIFIED**다.

## 9. 모니터링과 장애 대응

holdings fire의 status 줄(`schedules.jsonl`의 `last_status`, scheduler 이벤트 ledger의 `status`)이 측정원이다. 둘 다 금액이 없는 짧은 단어다. 외부 보고에는 이 단어의 횟수와 reason_code만 쓴다.

| 지표 | status 단어 |
|---|---|
| BLOCKED 알림 전달 | `h6 blocked told` |
| 전달 실패 | `h6 blocked not sent:<reason_code>` (채널 오류 포함) |
| told 기록 실패 | `h6 blocked told not recorded:<클래스>` |
| 오래된 기록 거부 | `h6 blocked told not recorded:stale` |
| 해소 보류 | `h6 blocked clearing withheld (nothing verified)` / `(the ledger began after the failure was told)` |
| 판정 실패 | `h6 blocked check failed (<클래스>)` |
| told 기록 손상 | `h6 blocked told record unreadable (…)` |
| 원장 검증 실패 | Telegram 본문의 reason_code와 category(`verify`/`load_state`/`legacy`/`check`), 보드의 `cash_flows.error` |
| fire 지연 | 기존 scheduler 이벤트의 시각 |

- **측정 한계:** 잠금을 얻지 못하면 status에는 `told not recorded:PersistenceError`만 남고, `STATE_LOCKED`라는 이름은 나오지 않는다(scheduler가 예외 클래스 이름을 적는다). STATE_LOCKED 횟수를 따로 세려면 코드 변경이 필요하고, 이번 범위가 아니다.
- **반복 알림:** 같은 장애가 이어지는 동안 반복은 없다. 장애 종류가 바뀌면 다시 알린다(알려진 반복, #1216 history).
- **대응:**
  - BLOCKED가 오면 원인 확인은 Thomas 터미널에서 한다(`holdings_board`의 터미널 전용 모드). Claude는 원장을 열지 않는다.
  - 같은 `not sent`나 `not recorded`가 연속 두 fire 나오면 Thomas에게 보고한다. 자동 rollback은 하지 않는다(H6 release와 같은 규칙).

## 10. 판정

문서 준비 상태와 실제 배포 허가 상태를 나눈다. CI 통과와 이 문서의 병합은 배포 권한을 주지 않는다.

| 구분 | 판정 |
|---|---|
| 문서 준비 | **DRAFT — PREPARATION_COMPLETE_PENDING_EVIDENCE**. 계획은 갖췄고 증거가 비어 있다 |
| 운영 배포 | **DEPLOYMENT_HOLD** |

**DEPLOYMENT_HOLD인 이유**

1. Global Guard 적용 증거가 없다(GLOBAL_GUARD_UPDATE_PENDING).
2. 운영 이미지(UNKNOWN)와 Rollback(ROLLBACK_NOT_VERIFIED)의 증거가 없다.
3. Guard를 지키는 이미지 내부 동작 검증 경로가 정해지지 않았다(VALIDATION_PATH_UNRESOLVED).
4. Thomas의 운영 배포 승인이 없다(NOT_GRANTED).

**미해결 항목**

| 항목 | 상태 |
|---|---|
| Global Guard | GLOBAL_GUARD_UPDATE_PENDING |
| 현재 운영 이미지 | UNKNOWN |
| Rollback | ROLLBACK_NOT_VERIFIED |
| Candidate 태그 | CANDIDATE_TAG_DECISION_REQUIRED(기본 권고 `candidate-1217`) |
| 이미지 내 동작 검증 | SEPARATE_APPROVAL_REQUIRED(VALIDATION_PATH_UNRESOLVED) |
| 운영 배포 승인 | NOT_GRANTED |

코드와 합성 테스트 기준으로 안전성 불변식(원장 불변, 거래 권한 불변, LOCAL_ONLY, at-least-once)이 의심스러운 곳은 없다. HOLD는 그 불변식 때문이 아니라 위의 증거·경로·승인이 비어 있기 때문이다.

**DECISION_REQUIRED**

1. candidate 태그: 기본 권고 `candidate-1217`, 대안 `candidate-1216`. 어느 쪽이든 코드 식별은 §4.1의 다섯 값으로 한다.
2. 이미지 내 동작 검증 경로: SYNTHETIC_RUNTIME_VALIDATION을 어떤 승인된 경로로 할지. Guard 정책의 예외나 우회는 이 결정에 포함되지 않는다.

## 11. 하지 않은 것

이미지 빌드·태그, 컨테이너 접근·재시작, Global Guard 설치·수정·kit 실행, preflight 실행, 실제 Telegram 발송, 원장·상태 파일 열람, Secret 조회, PR-A·B·C, H6c, 자동 리밸런싱, cutover, 거래 단계 변경.
