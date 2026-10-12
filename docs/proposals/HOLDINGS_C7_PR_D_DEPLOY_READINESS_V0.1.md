# 제안: C7 PR-D 운영 배포 준비 패킷 — 무엇이 실리고, 무엇을 확인하고, 어떻게 되돌리나 (DRAFT v0.1)

**상태:** DRAFT 2026-10-12 — C7 PR-D(#1216)를 운영에 올리기 전의 준비 패킷이다. 문서 준비 판정은 PREPARATION_COMPLETE_PENDING_EVIDENCE이고, 운영 배포 판정은 따로 DEPLOYMENT_HOLD다(§10). 2026-10-12 갱신(검토 기준 main `d97db39c`): Global Guard는 설치 동일성과 훅 연결이 Thomas 운영자 증거로 확인됐다(GLOBAL_GUARD_VERIFIED). Gate 2는 2026-10-12 Thomas가 Guard kit 직접 합성 시험 결과를 수용해 충족됐다(GATE_2_FULLY_SATISFIED, 증거 GATE_2_DIRECT_SYNTHETIC_TEST_PASSED). 새 Claude 세션의 told 파일명 실탐침은 하지 않았다(NEW_SESSION_TOLD_PROBE_NOT_PERFORMED). 이미지 자가검증 진입점은 구현됐고 저장소 합성 테스트를 통과했지만(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED), candidate 안에서는 돌리지 않았다. 운영 이미지와 여덟 서비스는 읽기 전용 메타데이터로 식별했다(IDENTIFIED_METADATA_ONLY, 이미지 안 코드 내용은 미검증). 배포를 막는 것은 Rollback(ROLLBACK_NOT_VERIFIED), candidate 안의 동작 검증과 D-10 격리 증거(DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED), 배포 승인(NOT_GRANTED)이다. DECISION_REQUIRED가 셋 있다: candidate 태그(CANDIDATE_TAG_DECISION_REQUIRED, 자가검증 #1221 병합 뒤 권고는 `candidate-1221`), 진입점의 candidate 격리 실행 승인(§4.3, 설계 문서 §2.4 V-4), 운영 preflight를 누가 어떤 범위로 돌리는지(§4.4, PREFLIGHT_EXECUTION_POLICY_DECISION_REQUIRED). 2026-10-12 preflight 안전성 검토: Guard 충돌은 없다. 읽지 못한 사실이 PASS나 통과 가능한 WARN이 되던 fail-open을 고쳤다(1차 다섯 곳, 2차 라벨 불일치·`docker ps` 실패·서비스 불일치의 promote STOP). Secret 없는 검증은 정적 계약까지만 가능하다(SECRET_FREE_PARTIAL_VALIDATION_ONLY, §4.4).

**기준:** 두 기준선이 있다.
- **설계 기준선:** origin/main `1f328ffd`(#1217 병합 시점). §2의 변경 범위와 §3–§9의 계획은 이 시점의 코드와 문서를 읽은 결과이고, 역사적 기준으로 남긴다.
- **검토 기준선:** origin/main `d97db39c`(#1218 병합 이후). 이번 갱신(#1219)의 기준이다. `d97db39c`는 `1f328ffd`에 문서만 더한 것이라 이미지에 들어가는 파일은 같다.

문서 기준 Git SHA는 운영 이미지의 코드 버전이 아니다(이미지에는 SHA 라벨이 없다, §1). 갱신 내용: 운영 메타데이터를 2026-10-12T06:15Z에 읽기 전용으로 관측했다(`docker ps`, `docker inspect`·`docker image ls`의 지정 필드만, env 미출력). 컨테이너 안 실행(`exec`/`run`), 상태 파일, 원장, 거래소 API에는 접근하지 않았다.
**소유:** 배포 절차의 권위는 `.claude/skills/deploy/SKILL.md`와 `scripts/ops/deploy_preflight.py`, PR-D 설계의 권위는 `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §6이다. 이 패킷은 둘을 바꾸지 않는다.

---

## 0. 한 줄

- **실리는 것은 origin/main 전체다.** 이미지에 들어가는 변화는 #1210 이후 네 파일뿐이다(§2).
- **코드 동작이 바뀌는 곳은 holdings fire 하나지만, 배포가 건드리는 것은 여덟 서비스다(§3).** 장애가 없으면 새 메시지도, 새 파일도, 새 status 단어도 없다.
- **문서 준비와 배포 허가는 다르다.** 문서는 PREPARATION_COMPLETE_PENDING_EVIDENCE이고, 배포는 DEPLOYMENT_HOLD다. Global Guard는 확인됐고 지금 도는 이미지는 메타데이터로 식별했다(2026-10-12). Rollback 확인, 승인된 이미지 내 동작 검증 경로, candidate 태그, 배포 승인이 없다. 안전성 불변식이 코드에서 의심스러운 곳은 없다. HOLD는 그 때문이 아니라 위의 빈칸 때문이다.
- **CI 통과와 이 문서의 병합은 배포 권한을 주지 않는다.**

## 1. 기준선

| 항목 | 값 | 근거 |
|---|---|---|
| 승인된 코드 | #1216 → `52b744e9` | MERGED 2026-10-12T03:17Z |
| 보안 보완 | #1217 → `1f328ffd` | MERGED 2026-10-12T03:59Z |
| 설계 기준선 main | `1f328ffd357f1cd7f8c304e9c882ad634ef6f92a` | #1217 병합 시점. 역사적 기준(§2의 범위 분석) |
| 검토 기준선 main | `d97db39c33e13ed163712bc5a34dea0ac4c5542d` | #1218 병합 이후, 이번 갱신의 기준. 필수 CI 5개·Safety invariants 성공 |
| 현재 운영 이미지 | **IDENTIFIED_METADATA_ONLY** | 관측 2026-10-12T06:15Z: 여덟 서비스 모두 `sha256:67d9b622d648f41e29c81fc10d1f2ecded98867fb697b5995e4781c8e47f29b3`. 이미지 안 코드 내용은 확인하지 않았다(§2) |
| 현재 운영 candidate | **`candidate-1210`** (= `latest`) | 같은 id에 붙은 태그. 이미지 생성 2026-10-10T10:12:45Z. 이미지에는 git SHA 라벨이 없다(Dockerfile에 `LABEL` 없음) |
| 신규 candidate | NOT_BUILT | |
| Rollback 이미지 | **ROLLBACK_NOT_VERIFIED** (원본 식별됨) | 이번 배포의 rollback 원본은 지금 도는 `67d9b622d648`이고, `latest`가 그것을 가리킨다(관측 시점). `rollback-pre-<N>` 태그는 아직 없다. 기존 `rollback-pre-1210` = `cb57fab0baba`(=`candidate-1206`)는 이전 배포의 rollback 점이다. 태그는 배포 시점 preflight가 다시 정하고, 복구는 실행해 보지 않았다 |
| Global Guard | **GLOBAL_GUARD_VERIFIED** (설치·연결) | 증거 수준은 세 가지로 나뉜다(§5 Gate 2). (A) GLOBAL_GUARD_INSTALLATION_VERIFIED: main·kit·설치본 sha256 모두 `88addc9d6d2e94e9f88a864ec27dcdef0f833e6dff4e894629cbaf712690fc86`, `refresh_guard.py check` exit 0. (B) GLOBAL_GUARD_HOOK_WIRING_VERIFIED: `test_guard.py` ALL PASS, 새 세션에서 기존 경로 탐침 3개 거부. 기존 규칙이라 told 규칙 자체의 직접 검증은 아니다. (C) TOLD_FILENAME_PROTECTION_VERIFIED_BY_CODE_IDENTITY_AND_CI: 간접 검증. (D) GATE_2_DIRECT_SYNTHETIC_TEST_PASSED(2026-10-12): kit `test_guard.py`(sha256 `d1ae8d77055d3799ce5eb98701c84b45780c1410bd1cb8016e94bf1b82c9dab4`)에 told 파일명 DENY 3건·snapshot ALLOW 1건을 더해 설치본과 전역 wrapper로 실행했다. 결과는 `ALL PASS`, exit 0, 실행 전후 설치본 sha 불변. 새 세션 실탐침은 하지 않았다(NEW_SESSION_TOLD_PROBE_NOT_PERFORMED). rollback 점 `guard-local-only.sh.pre-1217`(옛 `865e9eec…`). 근거: Thomas 운영자 증거 2026-10-12 |
| Candidate 태그 | **CANDIDATE_TAG_DECISION_REQUIRED** | `candidate-1217`은 잠정 권고(PROVISIONAL_RECOMMENDATION, §4.1). 자가검증 PR이 묶음에 들어오므로 최종 배포 기준선에서 다시 정한다 |
| 이미지 내 동작 검증 | **VALIDATION_PATH_UNRESOLVED** (진입점 구현됨) | 정식 진입점 `scripts/c7_blocked_selftest.py`는 구현됐고 저장소 합성 테스트를 통과했다(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED, 2026-10-12). candidate 안 실행과 D-10 격리 증거는 별도 승인이다(§4.3) |
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
- **갱신(2026-10-12):** 자가검증 PR은 `COPY scripts/`로 이미지에 다섯 번째 파일 `scripts/c7_blocked_selftest.py`를 더한다(`cash_flows`를 import한다). 이 파일에 닿는 schedule이나 door는 없다(`test_nothing_in_the_runtime_imports_the_door`). 위의 "네 파일"은 최종 배포 기준선에서 다시 계산한다(설계 문서 §3.5).
- 이 기준은 "운영 이미지가 `b6d4bf30`에서 빌드됐다"는 가정 위에 있다. 시각은 이 가정과 맞는다: `b6d4bf30`은 2026-10-10T09:55:29Z, 이미지 생성은 10:12:45Z, 그 다음 main 커밋(`c9bcf1af`, 이미지 경로 변화 없음)은 16:27:04Z다. 그러나 시각은 내용의 증거가 아니다. 이미지에 SHA 라벨이 없고 컨테이너 안을 열어 보지 않았으므로, 배포 때 §4.3의 CODE_IDENTITY_VERIFICATION이 이 가정을 대신한다.
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

### 3.2 여덟 서비스 관측 (2026-10-12T06:15Z, 읽기 전용 메타데이터)

`docker ps`와 `docker inspect`의 지정 필드만 읽었다(env 미출력, 컨테이너 안 실행 없음).

| 컨테이너 | 이미지 | 시작 | health | 재시작 |
|---|---|---|---|---|
| `thomas-operator` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-scheduler` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-scheduler-maint` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-read-bridge` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-dispatch-bridge` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-pipeline-worker` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-knowledge-bridge` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |
| `thomas-switch-bridge` | `67d9b622d648` | 2026-10-10T10:13:44Z | healthy | 0 |

- 여덟 서비스의 compose 라벨이 모두 같다.
  - 프로젝트 `thomas_agent`
  - compose 파일 `/root/deploy-1210/docker-compose.yml`
  - env 파일 `/root/thomas_agent/.env`

  다음 배포도 같은 프로젝트와 env 파일로 해야 같은 컨테이너를 다시 만든다(스킬 7단계).
- `hermes`는 별도 이미지(`hermes-agent`, `5539d8870610`)이고 이 묶음 밖이다.
- §3.1은 여덟 서비스가 한 이미지를 쓰므로 rollback 태그 하나로 모두 되돌릴 수 있다고 전제한다. 관측은 이 전제와 맞는다.
- 이 표는 관측 시점의 사실이다.
  - 배포 직전에는 preflight로 다시 읽는다.
  - 진행 중인 fire와 작업은 이 관측에 들어 있지 않다.

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
- **갱신(2026-10-12):** Thomas의 조건부 승인으로 정식 진입점 `scripts/c7_blocked_selftest.py`를 구현했다(설계 문서 §2.2–§2.5의 기준 D-1–D-10). 저장소 합성 테스트를 통과했다(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED). candidate 이미지 안에서는 아직 돌리지 않았으므로 상태는 VALIDATION_PATH_UNRESOLVED 그대로다.
- **보완(2026-10-12, 병합 전, Thomas 승인):** 독립 검토에서 나온 결함 둘을 고쳤다(설계 문서 §2.6).
  - 소스 무결성: 덮는 소스가 없거나 읽히지 않거나 소스 루트 밖으로 풀려도 `RESULT PASS`가 나올 수 있었다. 이제 `FAIL source_integrity`, `RESULT FAIL`, exit 1이고 메시지·traceback이 없다.
  - 합성 루트: `TemporaryDirectory`가 Python의 임시 디렉터리 선택(`TMPDIR`)을 따랐다. 이제 부모를 `/tmp`로 고정했고 fallback이 없다. D-10의 `/tmp` tmpfs 규칙과 맞는다.
  - 둘 다 부정 테스트로 고정했다. Windows CI는 모듈 상수만 바꿔 같은 테스트를 돌린다(skip 없음). door 예외(`IMPORT_ONLY_EXEMPT`)와 Guard는 그대로다. candidate 안에서는 여전히 돌리지 않았다.

### 4.4 Preflight 안전성 검토 (2026-10-12, 코드·합성 fixture만)

`deploy_preflight.py`는 실행하지 않았다. 코드, 그 테스트, Guard 규칙, compose 파일을 읽었다.

**검사별 분류**

| 범주 | 검사 | 필요한 데이터 | Guard | 이번에 확인한 것 |
|---|---|---|---|---|
| SAFE_STATIC | `pr`(병합, 병합 커밋이 origin/main에 있음), `tree`(깨끗한 origin/main), 이미지 경로 diff, 소스 해시, Dockerfile·compose 정적 검토 | git, GitHub | 걸리지 않음 | 확인함: #1221 = `abda884f` = origin/main. 이미지 경로 변경 5개 파일(§2) |
| OPERATOR_METADATA_REQUIRED | `tags`, `rollback`, `promote`(`docker images`, 실행 중 이미지 id), `compose`(라벨), `services`(`docker ps` 이름) | Docker 데몬 메타데이터 | 걸리지 않음 | 확인하지 않음. 과거 관측은 §3.2(06:15Z)뿐이다 |
| PROTECTED_OPERATIONAL_EVIDENCE | `volumes`(`.env` 보간 compose config), `fires`(`runtime_ledger/scheduler_events.jsonl` 꼬리, `schedules.jsonl`) | 운영 `.env`의 값, 운영 상태 파일 | 걸리지 않음(아래) | 확인하지 않음 |

- **Guard와의 관계:** Guard는 holdings 상태 디렉터리와 holdings 파일 이름만 막는다. `schedules.jsonl`은 허용 대상으로 명시돼 있고, `scheduler_events.jsonl`은 holdings 규칙 밖이다. holdings fire가 이벤트에 남기는 상태 줄(`store.refresh`의 `status`)은 상태 단어·예외 이름·reason code뿐이며 금액은 없다(코드로 추적했다). preflight는 그중 `kind`, `schedule_id` 끝 8자, `created_at`만 출력한다. 따라서 **Guard 규칙상으로는 막히지 않지만, 운영 상태이므로 실행에는 승인이 필요하다.**
- **compose config의 노출 범위:** compose 파일은 `${…}`로 `.env`의 키를 보간한다. 여기에는 거래소 주문 키(`MVP_LIVE_ORDER_API_SECRET` 등), Toss, Telegram, provider 키가 포함된다. 따라서 volume 검사의 출력은 이 값들을 **preflight 프로세스 메모리 안에서** 담는다. 스크립트는 서비스 이름, 컨테이너 이름, bind source만 꺼내고 출력하지 않는다(테스트로 고정). 이 검사는 `THOMAS_STATE_DIR` 보간 없이는 성립하지 않으므로 그대로 둔다. 같은 보간은 배포의 `compose up` 자체에서도 일어난다.

**고친 결함 (fail-open → fail-closed).** 필수 검사는 하나도 지우지 않았다. STOP을 WARN으로 바꾼 곳도 없다. 첫 실행의 WARN은 유지했다(첫 실행의 factory 예정 포함).

| 결함 | 전 | 후 |
|---|---|---|
| `docker images` 실패 | 빈 목록으로 읽혀 `tags`가 PASS("free")를 냄 | `tags`·`rollback`·`promote` STOP("unverified") |
| scheduler 상태 파일을 읽지 못함 | 두 실행 모두 WARN, `DEPLOY_PREFLIGHT=OK` 가능 | `--promote`는 STOP, 첫 실행은 WARN |
| 짝 없는 `started`의 시각을 해석하지 못함 | 무시됨 | `--promote`에서 진행 중으로 봄(STOP) |
| compose 라벨을 읽지 못함 | WARN | `--promote` STOP, 첫 실행 WARN |
| compose 라벨 불일치(project, env file) | WARN | `--promote` STOP, 첫 실행 WARN (Thomas 결정 2026-10-12, 2차 보완) |
| `docker ps` 실패 | 빈 목록으로 읽혀 "not running" WARN | 실패를 빈 목록과 구분한다. `--promote` STOP, 첫 실행 WARN (2차 보완) |
| 실행 중인 `thomas-` 컨테이너 집합이 tree compose의 서비스와 다름(누락, 이름 없는 `thomas-` 컨테이너) | WARN | `--promote` STOP, 첫 실행 WARN (2차 보완). 비교 대상은 compose의 서비스 전부라서 hermes가 꺼져 있어도 STOP이다(`up -d`가 hermes를 새로 시작하게 되므로). 다른 프로젝트 컨테이너(`thomas-` 접두사 없음)는 비교하지 않는다 |
| gh·compose 출력이 JSON이 아니거나 모양이 다름 | traceback | `pr`/`volumes` STOP. 출력 내용은 찍지 않음 |

2차 보완 테스트는 수정 전 코드(`907faf64`)에서 9건이 실패한다. 합성 테스트는 가짜 `.env` 값을 compose 출력에 넣어 검증한다. PASS, bind 이상, JSON 오류 어느 경로에서도 그 값이 stdout과 stderr에 나오지 않는다. 그 밖에도 위 표의 각 경우와 두 실행의 필수 검사 목록이 그대로인지를 고정했다. 수정 전 코드에서는 새 테스트 15건이 실패한다.

**Secret 없는 검증 경로: SECRET_FREE_PARTIAL_VALIDATION_ONLY (2026-10-12 검토)**
- **정적으로 증명한 것** (`.env` 값은 읽지 않았다. 원본 compose를 테스트로 고정):
  - bind source 26개가 모두 `${경로변수:-기본값}[/하위경로]` 꼴이다.
  - 쓰이는 변수는 경로 변수 여섯 개뿐이다(`THOMAS_STATE_DIR`, `THOMAS_CORE_ACTIVATIONS_DIR`, `THOMAS_CORE_APPROVALS_DIR`, `THOMAS_WORKSPACE_DIR`, `THOMAS_BLOG_VAULT_DIR`, `HERMES_DATA_DIR`). 고정된 호스트 경로나 credential 변수는 없다.
  - 런타임 서비스 여덟 개가 모두 상태 루트를 `THOMAS_STATE_DIR` 하나에서 mount한다.
  - 기본값이 상대 경로다. 그래서 변수가 비어 있으면 tree 안으로 풀리고, 배포 시점 volume 검사가 STOP한다.
- **증명하지 못한 것:** 여섯 변수가 실제로 무엇으로 풀리는지. 이 값의 근거는 `.env`뿐이다. compose는 변수 일부만 골라 보간하지 못한다. 그래서 기존 volume 검사와 같은 효력을 가지려면 `.env` 보간이 필요하다. 정적 계약만으로는 Volume Gate를 충족하지 못하며, 배포 시점 volume 검사가 그대로 필수다.
- **판단에 필요한 사실:** 배포 7단계의 `compose up -d`도 compose 프로세스 안에서 같은 `.env`를 보간한다. 따라서 정할 것은 `.env`를 보간할지 여부가 아니다. preflight의 Python 프로세스가 그 보간 결과를 **추가로** 받아도 되는지다.
- 실행 중 컨테이너의 mount나 파일 메타데이터에서 경로 값을 추론하는 경로는 쓰지 않았다. 지금 컨테이너가 맞다는 사실이 새 compose가 맞다는 증거는 아니기 때문이다.

**실행 판단 (PREFLIGHT_EXECUTION_POLICY_DECISION_REQUIRED)**
- preflight 없이는 배포하지 않는다(§6).
- 운영 preflight는 B·C 범주를 읽는다. 이번 승인은 그것을 제외했으므로 이 범주는 미충족이다.
- 정해야 할 것:
  - 누가 돌리는가. 스킬은 배포하는 세션이 돌린다고 정한다.
  - Claude 세션이 `.env` 보간을 포함한 이 스크립트를 돌려도 되는가.
  - 출력은 스크립트의 판정 줄만 쓴다.
- 보호된 접근을 다른 스크립트·실행기·사람으로 옮기는 방식은 제안하지 않는다.

## 5. 배포 전 Gate

| Gate | 조건 | 현재 |
|---|---|---|
| 1 코드 무결성 | #1216·#1217이 main에 있음, 필수 CI 5개 PASS, Safety invariants PASS, Release Gate PASS, candidate가 기준 SHA로 연결됨 | main과 CI는 **충족**. candidate 연결은 빌드 때 CODE_IDENTITY_VERIFICATION(§4.3)으로 확인 |
| 2 Global Guard | 설치본 sha = main의 Guard sha, kit `check` exit 0, kit `test_guard.py` ALL PASS, told 파일명 합성 DENY, `holdings_snapshot.json` 합성 ALLOW, 새 세션 적용 확인, `.prev` 백업 존재 | **충족 — GATE_2_FULLY_SATISFIED**(Thomas 수용 2026-10-12).<br>설치본 sha, kit `check` exit 0, 새 세션 적용, 백업은 이전부터 충족했다(GLOBAL_GUARD_INSTALLATION_VERIFIED, GLOBAL_GUARD_HOOK_WIRING_VERIFIED). 백업은 `.pre-1217`이고 `.prev`는 보존했다.<br>**told 파일명 합성 DENY와 `holdings_snapshot.json` 합성 ALLOW:** kit에 Bash·Read·Monitor DENY 3건과 snapshot ALLOW 1건을 더했다(kit sha256 `1922dcfe…` → `d1ae8d77055d3799ce5eb98701c84b45780c1410bd1cb8016e94bf1b82c9dab4`, 사본 `test_guard.py.pre-told`). 설치본과 전역 wrapper로 실행한 결과는 다음과 같다(GATE_2_DIRECT_SYNTHETIC_TEST_PASSED).<br>- 설정 6/6, DENY 15+3, ALLOW 10+1, Guard가 없을 때의 동작 3/3<br>- `ALL PASS`, exit 0<br>- 실행 전후 설치본·kit·설정 sha 불변<br>조건은 완화하지 않았고, 직접 합성 검증으로 채웠다.<br>**새 Claude 세션의 told 파일명 실탐침은 하지 않았다(NEW_SESSION_TOLD_PROBE_NOT_PERFORMED).** kit 합성 시험과 같은 증거로 적지 않는다. 이전 레이블 DIRECT_TOLD_FILENAME_PROBE_NOT_PERFORMED는 이 두 기록으로 대체됐다 |
| 3 LOCAL_ONLY | 준비 확인이 실제 금융 데이터 없이 가능, Secret 노출 없음, 새 외부 출력 없음, 자동 원장 복구 없음 | **충족(코드 기준)**. 새 외부 출력은 Telegram 문구 하나이고 키·금액·계정이 없다(테스트 고정). told 파일은 상태 디렉터리 안 |
| 4 Runtime 호환성 | 원장·상태 형식 변화 없음, told 파일 초기 생성 계약, 잠금 계약, 기존 scheduler 동작 유지 | **충족(코드와 합성 테스트 기준)**. PR-D는 told 파일만 쓴다. 잠금은 기존 `_state_lock`이다. 판정은 잠금을 잡지 않는다 |
| 5 운영자 알림(소스) | 최초 BLOCKED, 지속 중 중복 없음, 전송 실패 시 재알림, 기록 실패 시 재알림, 해소 1회, 재발 재알림, 파일 소실 시 잘못된 해소 없음, 금융 데이터 비노출 | **충족(합성)**. `tests/test_mvp_runtime_holdings_c7_blocked.py` 47건이 각 항목을 덮는다. 실제 채널 확인은 SEPARATE_APPROVAL_REQUIRED |
| 5b 이미지 내 동작 검증 경로 | Guard를 지키고 실제 데이터가 없는 SYNTHETIC_RUNTIME_VALIDATION 경로가 승인됨 | **DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED**. 승인된 경로가 없다(§4.3) |
| 6 Rollback과 서비스 재생성 | 운영 이미지 식별, rollback 이미지 존재, 명령 검토, 형식 호환, 비가역 변경 없음, 사후 확인 방법. 여기에 §3.1의 항목: 여덟 서비스의 기존 이미지 id, 실제 재생성 대상, 진행 중인 fire와 작업, 서비스별 health 방법, 원래 이미지와 rollback 태그의 일치, 배포 뒤 여덟 서비스의 이미지 id 일치 | **ROLLBACK_NOT_VERIFIED**. 명령·호환성·비가역성은 §8에 검토했다. 메타데이터는 §3.2에 관측했다(여덟 서비스 같은 이미지, healthy, 재시작 0, 같은 compose 프로젝트). 진행 중인 fire·작업, rollback 태그 생성, 복구 실행은 배포 시점 preflight와 운영자의 몫이다 |
| 7 운영자 승인 | Thomas의 별도 배포 승인 | **NOT_GRANTED**. 병합, CI PASS, Guard VERIFIED는 배포 승인이 아니다 |

**Gate 2가 배포 앞에 오는 이유:** 배포 뒤 told 파일이 실제로 생긴다. 상태 디렉터리 안에서는 설치본의 경로 규칙이 이미 막는다. 그러나 이름만 쓴 참조나 다른 경로의 사본은 새 규칙이 있어야 막힌다(#1217).

## 6. 배포 중단 조건

다음 중 하나면 그 자리에서 멈추고 보고한다. 우회하지 않는다.

- preflight의 어떤 STOP. 특히 `promote`(다른 세션의 재배포)와 `fires`(진행 중인 fire, 또는 10분 안의 factory fire: 매일 08:09–08:52 UTC)
- CODE_IDENTITY_VERIFICATION(§4.3)에서 불일치
- 승인된 이미지 내 동작 검증 경로가 없음: **DEPLOYMENT_HOLD — VALIDATION_PATH_UNRESOLVED**
- 여덟 서비스의 지금 이미지 id가 서로 다르거나, rollback 태그가 그 이미지를 가리키지 않음
- operator·pipeline-worker·bridge에 끊으면 안 되는 작업이 진행 중임(운영자 판단)
- Gate 2가 PENDING, FAILED, 또는 완전히 충족되지 않음(GATE_2_NOT_FULLY_SATISFIED → DEPLOYMENT_HOLD, 기존 조건의 Fail-Closed 해석. 2026-10-12 현재 Gate 2는 충족)
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
| 문서 준비 | **DRAFT — PREPARATION_COMPLETE_PENDING_EVIDENCE**. Global Guard, Gate 2, 운영 메타데이터, 자가검증 진입점은 채웠다. Rollback 확인, candidate 안 검증, 태그, 승인이 남았다 |
| 운영 배포 | **DEPLOYMENT_HOLD** |

**DEPLOYMENT_HOLD인 이유**

1. Rollback이 확인되지 않았다(ROLLBACK_NOT_VERIFIED). 원본 이미지는 식별했지만 rollback 태그와 복구는 배포 시점의 일이다.
2. 이미지 내부 동작 검증이 끝나지 않았다(VALIDATION_PATH_UNRESOLVED). 진입점은 구현했고 저장소 합성 테스트를 통과했다(VALIDATION_ENTRYPOINT_IMPLEMENTED, REPOSITORY_SYNTHETIC_TESTS_PASSED). 그러나 candidate 안 실행과 D-10 격리 증거는 아직이다.
3. candidate 태그가 정해지지 않았다(CANDIDATE_TAG_DECISION_REQUIRED).
4. Thomas의 운영 배포 승인이 없다(NOT_GRANTED).

**미해결 항목**

| 항목 | 상태 |
|---|---|
| Global Guard 설치·연결 | **해소** — GLOBAL_GUARD_VERIFIED(설치 동일성, 훅 연결) |
| Gate 2 | **해소** — GATE_2_FULLY_SATISFIED(GATE_2_DIRECT_SYNTHETIC_TEST_PASSED, Thomas 수용 2026-10-12) |
| 새 세션 told 실탐침 | **미수행** — NEW_SESSION_TOLD_PROBE_NOT_PERFORMED(필수 조건 아님, Thomas 결정) |
| 현재 운영 이미지 | **식별** — IDENTIFIED_METADATA_ONLY(`67d9b622d648`, 코드 내용 미검증) |
| 여덟 서비스 메타데이터 | **관측** — 같은 이미지, healthy, 재시작 0(§3.2) |
| Rollback | ROLLBACK_NOT_VERIFIED(원본 식별됨) |
| Candidate 태그 | CANDIDATE_TAG_DECISION_REQUIRED(`candidate-1217`은 잠정 권고. 자가검증 PR이 묶음에 들어오면 다시 정함) |
| 이미지 내 동작 검증 | VALIDATION_PATH_UNRESOLVED. 진입점 `scripts/c7_blocked_selftest.py` 구현·저장소 테스트 통과. candidate 실행과 D-10 격리 증거는 별도 승인 |
| 운영 배포 승인 | NOT_GRANTED |

코드와 합성 테스트 기준으로 안전성 불변식(원장 불변, 거래 권한 불변, LOCAL_ONLY, at-least-once)이 의심스러운 곳은 없다. HOLD는 그 불변식 때문이 아니라 위의 증거·경로·승인이 비어 있기 때문이다.

**DECISION_REQUIRED**

1. candidate 태그: 기본 권고 `candidate-1217`, 대안 `candidate-1216`. 어느 쪽이든 코드 식별은 §4.1의 다섯 값으로 한다.
2. 이미지 내 동작 검증: 구현된 진입점을 candidate에서 D-10 격리로 실행할지(설계 문서 §2.4 V-4). Guard 정책의 예외나 우회는 이 결정에 포함되지 않는다.

## 11. 하지 않은 것

이미지 빌드·태그, 컨테이너 안 실행(`exec`/`run`)·재시작(메타데이터 읽기만 했다), Global Guard 설치·수정·kit 실행(Thomas가 했다), preflight 실행, 실제 Telegram 발송, 원장·상태 파일 열람, Secret 조회, PR-A·B·C, H6c, 자동 리밸런싱, cutover, 거래 단계 변경.
