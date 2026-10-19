# 제안: 호스트 권한 분리 (Option C) — Claude 계정, 배포 실행기, 승인 결속 (DRAFT v0.1)

**상태:** PARTIALLY DECIDED 2026-10-17 — **2026-10-17:** C3a-3 #1229(`2cf78cac`), C3b #1234(`c88708de`, 서비스 진입점·설정 로더·설치 도구·unit 템플릿), C4 계획 #1232(`66e24fde`)가 병합됐다. CR-8b(Python 3.12.13, python-build-standalone x86_64, SHA256 고정)와 D-8(GitHub Actions 임시 러너, 비필수 workflow)이 정해졌다. C4 시험 workflow는 구현·PR 생성까지 승인됐다(병합·운영 제외). 설치·계정 분리·운영은 승인되지 않았다(§4.25). **2026-10-16(4):** C2(승인 검증 인터페이스) 설계가 완료됐다(C2-FINAL, DESIGN_ONLY). 명세는 `HOST_PRIVILEGE_SEPARATION_C2_SPEC_V0.5.md`에 원문 그대로 옮겼다. 그 뒤 D-10a·CR-12 T3(조건부)·TYPE-B-KEY K1·D-3 C3a 범위·CR-8이 DESIGN_ONLY로 정해졌다. 실행기 코드는 C3a로 나눠 들어가고 있다: C3a-1 #1227·C3a-2 #1228 MERGED, C3a-3 #1229 병합 승인 대기. #1229는 사전 승인 없이 만들어졌고 2026-10-17에 사후 추인됐다(소급 승인 아님). 같은 날 C2 계약에 맞춘 수정이 승인됐고, C2 최소 개정 하나(`COMPOSE_PROJECT_MISMATCH` 신설)가 결정됐다. 설치·계정 분리·운영은 승인되지 않았다(§4.25). **2026-10-16(3):** Option C의 저장소 구현은 main에 있다. PR-B #1223(`8979e702`)과 PR-C #1224(`0b6fd69c`)는 MERGED이고, 두 PR 모두 PR CI·main CI 필수 5개가 SUCCESS다. 운영 배포는 없고 운영 적용은 HOLD, Claude read-bridge는 BLOCKED다. 명시 확정: D-11 a1(코드)·c1·d2·f2·e2(저장소 구현), crypto 예외(보안 버그 수정만), E-01 E1-a(문서만), E-02–E-04(코드 병합). 잠정: D-1·D-3·D-11 b3. 미해결: 실제 OS 권한 분리, read door 운영화, R-08, 진단 파일 격리, 배포(§4.24). **2026-10-16(2):** D-11 e2는 EXPLICITLY_APPROVED(저장소 내부 보안 구현만)이고, crypto 연구 중단 예외는 보안 버그 수정에 한정해 APPROVED됐으며, E-01은 E1-a(문서만), E-02–E-04는 저장소 보안 수정만 APPROVED됐다. 구현과 합성 시험은 끝났고 PR-C는 아직 만들지 않았다(§4.23). D-1·D-3·D-11 b3는 PROVISIONALLY_ACCEPTED다. **2026-10-16(1):** Thomas가 D-11 a1(코드 부분)·c1·d2·f2를 저장소 구현 범위로 명시 확정했다(EXPLICITLY_APPROVED). 이를 구현한 PR-B #1223은 squash 병합됐다(MERGED, main `8979e702`, PR·main CI 필수 5개 통과). 운영 적용은 HOLD이고 배포는 없으며 Claude read-bridge 접근은 BLOCKED다. 그때 D-11 e2는 아직 PROVISIONALLY_ACCEPTED였고 PR-C(scheduler e2)와 PR-A(이 문서)는 만들어지지 않았다(§4.22). 이전 상태(2026-10-15): 명시 결정은 Option A(2026-10-12)와 2026-10-15 지시(보안 검토 R-01 대안 (a) 단일 프로필, R-02 감사 범위, S-1·S-2 문구 정정. 저장소 안 코드·문서·합성 시험 한정)뿐이다(§4.18). 2026-10-13 권고 일괄 수용 지시를 근거로 D-11 a1·b3·c1·d2·e2(e-1–e-3 포함)·f2, D-1 대안 C, D-3 §4.3 범위 한정을 구현 방향으로 해석했으며, 항목별 명시적 확정은 대기 중이다(PROVISIONALLY_ACCEPTED, §4.17). 구현은 커밋하지 않았고 IMPLEMENTED_PENDING_REVIEW다. 운영 권한(OPERATIONALLY_AUTHORIZED)은 어떤 항목에도 없고 Claude read-bridge 접근은 BLOCKED다. R-03 결과 Claude 프로필은 투영된 2개 읽기만 서비스한다(§4.18.3). 2026-10-15 2차 지시로 2개 유지·4개 보류, 4개의 응답 계약 설계(SCRATCH 검증), R-07 구현, 무응답 연결의 감사 공백 유지, R-08 문서화를 EXPLICITLY_APPROVED 범위(저장소 안)로 진행했다. 4개를 프로필에 넣는 것은 별도 승인 대기다(§4.19). 2026-10-15 3차 지시로 R3-5(프로필 door 오류 응답을 코드별 고정 문구로), R3-1(heartbeat 구조화 실패 코드와 계약), R3-2(`ControlState` 기반 runtime_status 계약)를 저장소 안에 구현했다. 두 계약 모두 프로필에는 등록하지 않았다(§4.20). 열린 결정: D-2, D-4, D-5–D-10, D-12–D-16, Claude 계정 uid·gid, 요청 로그 디렉터리, 진단 파일 경로, 커밋·PR, 모든 운영 단계(C5·C6). 이전 상태(2026-10-12 DRAFT): 운영 Secret 정책은 Option A(현 정책 유지)로 결정됐다(Thomas 2026-10-12. 3-1·3-2 REJECT, B′ 미채택, Rollback 비상 권한과 운영 배포 NOT_GRANTED, DEPLOYMENT_HOLD). 이 문서는 장기 방향인 Option C를 실제로 추진하기 위한 설계다. Claude 세션을 root가 아니고 docker 그룹 밖인 계정으로 옮기고, 배포는 Claude가 고칠 수 없는 실행기가 고정된 동작만 수행하게 하고, 운영 변경 동작은 SHA·이미지 id에 결속된 Thomas 승인 없이는 거부한다. 권고안은 대안 C(§5): 최소 기능의 전용 실행기를 두고, 전송과 감사는 기존 socket door와 audit 라이브러리를 재사용한다. 대안 선택(D-1)은 PROVISIONALLY_ACCEPTED(대안 C, 항목별 확정 대기)이고 승인 채널(D-2, 권고 SSH 서명)은 미결정이다. 이 문서는 운영 권한을 주지 않는다. 호스트 계정·권한 변경, 실행기 설치, 부정 시험 실행은 각각 따로 승인받는다(§6). 결정 항목은 D-1–D-16이다(§8). 2026-10-13 C1: Claude의 root·Docker 의존 작업 30개를 정리하고 대체 경로를 정했다(§4.7: A 5, B 6, C 8, D 13, E 3, 일부 중복. W-14·W-15는 2026-10-13 소스 대조로 판정, §4.8.1. W-11·W-12는 dispatch door와 대조했으나 동등하지 않아 E 유지, §4.8.2. W-13은 READY 경로를 대조했으나 부분 동등이라 E 유지, §4.8.3. W-20은 사용처별 B·C·D로 판정, §4.8.4. W-30은 D로 재분류, §4.12.3. D-11 권고를 전용 인스턴스로 바꿨다, §4.12.1. 승인 전 검증에서 `scheduler_events`의 factory 실패 문구가 예외 메시지를 싣는 것을 확인했다, §4.12.4. 그 문구의 네 갈래 흐름, Claude용 최소 응답 계약, D-11 승인 조건 3단계, 테스트 X-1–X-10을 명세했다, §4.12.5. 2026-10-13 구현 준비: scheduler 읽기 응답 계약 v1(§4.13, 저장소 밖 참조 구현으로 10개 시험 통과와 결함 주입 검출 확인), D-11 결정 패킷(§4.14), W-11–W-13·W-30·D-16 정리(§4.15, D-16 정규식 대조)). read-bridge로 대체되는 읽기는 4건이지만 Claude 계정은 그 소켓에 아직 닿지 못한다(§4.8). cron 3줄과 Mac SSH 계정은 NOT_VERIFIED다(§4.9). Claude가 고칠 수 있는 스크립트가 지금 `.env`의 봇 토큰을 읽는다는 것을 찾았다(§4.9, D-12). 승인 서명의 위조 저항과 검증 우회를 나눠 과도한 주장을 고쳤다(§4.10).

**기준:** origin/main `1b45ed57`(#1222), 2026-10-12. 저장소의 소스·문서·테스트를 읽었다. 호스트는 메타데이터만 읽었다:
- `id`, `getent group docker`, uid 1000 이상 계정 목록, `sudo` 위치, `/etc/sudoers.d`·`/etc/cron.d`·`/etc/systemd/system`의 파일 이름
- `ps -eo user,comm`(사용자와 프로세스 이름만, 인자 없음)
- 소켓·디렉터리·`.env*` 파일의 소유자와 모드(`stat`)
- `ptrace_scope`, `/proc` 마운트 옵션
- root crontab은 주석이 아닌 줄 수와 "claude"를 포함한 줄 수만 셌다. 내용은 출력하지 않았다.

`.env`와 그 사본의 내용, 런타임 상태, 컨테이너, Docker 데몬에는 접근하지 않았다.

2026-10-13 C1 추가 조회(origin/main 그대로 `1b45ed57`):
- `/root/.claude/scripts` 파일 이름 목록과, docker·chown·sudo·`.env`·상태 루트를 언급하는 파일의 해당 줄
- `hermes-notify.sh` 머리 주석
- `/root/.claude/settings.json`에서 hook 이름과 스크립트 경로만
- `/root/obsidian-thomas`·`/root/.claude`·`/root/backups`의 소유자와 모드
- 이전 세션 메모리 기록(REPORTED_BUT_NOT_RECHECKED로만 인용)

crontab은 다시 읽지 않았다.
**소유:** 배포 절차의 권위는 `.claude/skills/deploy/SKILL.md`와 `scripts/ops/deploy_preflight.py`다. Secret 경계는 `docs/DEPLOYMENT.md`의 "Secret boundary"와 `tests/test_deployment_env_passthrough.py`가 정한다. door 전송은 `runtime/mvp_runtime/socket_door.py`가 정한다. 이 문서는 그것들을 바꾸지 않는다. 바꿀 곳은 §6 각 Phase가 이름으로 적는다. PR-D 배포 판정은 `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` §4.5가 이 문서를 가리킨다.

---

## 0. 한 줄

- 지금 Secret을 막는 것은 규칙(CLAUDE.md, Decision 3)과 명령 텍스트 tripwire(Guard)뿐이다. Claude는 root이고 Docker 소켓 소유자이므로 기술적 경계가 없다(§2, VERIFIED_FROM_SOURCE·호스트 메타데이터).
- 경계를 만드는 방법은 OS 계정 분리다. 2026-10-09 Guard 기록이 이미 같은 결론과 4단계를 적었다. 이 문서는 그 4단계를 설계 수준으로 구체화한다.
- 새로 만드는 것은 최소다. 전송(socket door + `SO_PEERCRED`), 감사(kernel audit 라이브러리), preflight 판정 로직(`deploy_preflight.preflight`)은 재사용한다. 새로 필요한 것은 실행기의 고정 동작 목록, 승인 검증, 응답 스키마다(§5).
- 분리의 비용도 크다. Claude는 직접 배포하지 못하고, `docker exec` 상태 CLI와 운영 상태 파일 읽기도 잃는다. 대신 holdings LOCAL_ONLY 보호가 처음으로 실제 경계가 된다(§4.1).

## 1. 이미 결정된 것 (재논의하지 않음)

| 항목 | 결정 (Thomas 2026-10-12) |
|---|---|
| 운영 Secret 정책 | Option A — 현 정책 유지 |
| 3-2 Preflight Secret 처리 예외 | REJECT |
| 3-1 Deployment·Rollback Secret 처리 예외 | REJECT |
| B′ (기술 격리 없는 정책 예외) | 채택하지 않음 |
| Rollback 비상 권한 | NOT_GRANTED |
| 운영 배포 | NOT_GRANTED, DEPLOYMENT_HOLD |
| ROLLBACK_FUNCTIONAL_VERIFIED | 부여하지 않음 |
| 장기 방향 | Option C — Claude 운영 권한과 Secret 접근 경계 분리 |

판단 근거(Thomas): Claude 실행 환경이 root와 Docker 데몬 접근을 갖는다. 명령 제한·출력 억제·로그 마스킹은 기술적 접근을 막지 못한다. preflight의 compose config 처리에서 Secret이 프로세스 메모리에 들어간다. B′는 기술 격리가 아니다.

과거 보고의 기준값(origin/main `1b45ed57`, Candidate Q `sha256:f7cb680b6781…c71`, Rollback P `sha256:67d9b622d648…9b3`, worktree `/root/deploy-1222`)은 2026-10-12 기록이다. 이 문서는 이미지 값을 다시 확인하지 않았다(Docker 접근 금지). origin/main은 2026-10-12 이 문서 작성 시 `git fetch`로 `1b45ed57`임을 확인했다.

## 2. 현재 구조 (조사 결과)

분류: VERIFIED_FROM_SOURCE(코드·문서를 읽어 확인), VERIFIED_FROM_TEST(테스트가 고정함, 이번에 실행 여부는 따로 적음), VERIFIED_HOST_METADATA(이번에 호스트 메타데이터로 확인), REPORTED_BUT_NOT_RECHECKED(과거 보고), DESIGN_ASSUMPTION, NOT_TESTED.

### 2.1 호스트 권한 (VERIFIED_HOST_METADATA, 2026-10-12)

| 사실 | 값 | 의미 |
|---|---|---|
| Claude 세션의 계정 | 이 세션은 `uid=0(root)`(`id`). 버전 번호 모양의 프로세스 이름 세 종류도 root로 실행 중이다. 이것이 Claude Code라는 판단은 이름에 근거한 추론이다 | 파일 모드는 Claude를 막지 못함 |
| 일반 사용자 계정 | uid 1000 이상 계정 없음 | Claude 전용 계정이 아직 없음 |
| Docker 소켓 | `srw-rw---- root:docker`, docker 그룹 구성원 없음 | root만 접근. 그 root가 Claude |
| `.env` | `600 root`. 같은 디렉터리에 `.env.bak-*` 사본 3개, 모두 `600 root` | 사본도 S1이다. 분리 후 실행기만 읽어야 함 |
| `/root` | `drwx------ root` | 비root 계정은 저장소, 모든 worktree, `.env`, 상태 루트에 닿지 못함. Claude 작업 공간을 옮겨야 하고, 실행기 계정도 같은 문제를 가짐(§4.2, D-10) |
| 상태 루트 `.runtime_governance_state` | `drwx------`, 소유자 uid 10001(호스트에 이름 없음) | root와 uid 10001만 읽음. preflight의 `tail`·`cat`(scheduler 파일 두 개)은 root 호출을 전제로 함 |
| sudo | `/usr/bin/sudo`, `/etc/sudoers.d/90-cloud-init-users` 있음 | Claude 계정은 어느 sudoers 파일에도 없어야 함 |
| root crontab | 비주석 15줄, "claude" 포함 3줄(내용 미확인) | cron이 띄우는 Claude도 옮겨야 함 |
| `ptrace_scope` | 1 | 다른 uid 프로세스의 ptrace는 이미 거부됨 |
| `/proc` | `hidepid` 없음 | 프로세스 이름은 보임. `environ`·`mem`은 소유 uid만 읽음 |
| 컨테이너 uid | 런타임 10001(Dockerfile `USER thomas`), hermes 10000 | Claude 계정은 0·10000·10001과 달라야 함 |

### 2.2 Preflight와 Secret 경로 (VERIFIED_FROM_SOURCE)

| # | 항목 | 발견 | 분류 |
|---|---|---|---|
| A | Preflight 인터페이스 | `deploy_preflight.py <PR> [--promote --tree <dir>]`. 판정 로직 `preflight(run, …)`은 명령 실행기 `run`을 주입받는다(`:85`, `:377`). 경로 상수는 `/root/thomas_agent` 고정(`:62-69`) | VERIFIED_FROM_SOURCE |
| B | Compose 검증이 필요한 이유 | bind source가 `${경로변수:-기본값}`이라 실제 값은 `.env`에만 있다. 상대 기본값으로 풀리면 스케줄러가 빈 상태 디렉터리로 뜬다(2026-08 사고). 그래서 `compose config`로 보간한 결과를 검사한다(`:241-275`) | VERIFIED_FROM_SOURCE, VERIFIED_FROM_TEST(`test_ops_deploy_preflight.py`의 bind 계약 테스트, 이번에 실행하지 않음) |
| C | Secret이 지나가는 프로세스 | ① `docker compose config` 프로세스와 그 stdout을 받는 preflight Python(`:241`) ② `compose up`의 compose 프로세스 ③ 각 컨테이너 env(`environment:` 열거, `env_file` 금지) ④ Docker API를 쓰는 모든 프로세스(`inspect`, `exec`) ⑤ `.env` 파일과 사본 | VERIFIED_FROM_SOURCE(①–③), VERIFIED_HOST_METADATA(⑤), DESIGN_ASSUMPTION(④: Docker API의 일반 동작) |
| D | Guard의 한계 | `guard-local-only.sh`는 명령 텍스트만 본다. 스스로 "tripwire, not a boundary"라고 적고, 런타임 경로 조립·디스크 스크립트·docker 그룹을 우회로로 든다(`:12-17`). `block-main-commits.sh`는 `cd`·`git -C`가 있으면 판단하지 않는다. 실제 강제는 `.githooks/pre-commit`. `.env`는 어떤 Guard·settings 규칙에도 없다 | VERIFIED_FROM_SOURCE |
| E | 기존 권한 분리·실행·승인·감사 | §2.3 표 | VERIFIED_FROM_SOURCE |
| F | 기존 테스트·fixture | preflight 94건(가짜 `run`으로 호스트를 흉내, 합성 `.env` 값 누출 검사 포함). door 전송 `test_mvp_runtime_socket_door_hardening.py`(`test_an_unpermitted_uid_is_refused` 등). door별 닫힌 동작 테스트(read·switch·dispatch·knowledge). hermes 경계 `test_deployment_env_passthrough.py` | VERIFIED_FROM_SOURCE(이번 작업에서 실행하지 않음) |
| G | 정책과 충돌하는 곳 | ① deploy skill과 CLAUDE.md "Deploying"은 배포 세션(Claude)이 `--env-file`로 compose를 돌린다고 정한다. Option A에서는 이 절차를 따를 수 없다 ② CLAUDE.md는 상태 CLI를 `docker exec thomas-scheduler …`로 돌리라고 한다. 분리 후에는 불가능하다 ③ verify skill의 Core 활성화는 `chown -R 10001:10001`을 쓴다 | VERIFIED_FROM_SOURCE |
| H | 재사용 가능한 구현 | socket door 전송과 `SO_PEERCRED`, 승인 저장소의 결속·만료·소비, kernel audit 해시 체인, preflight 판정 로직. §2.3과 §5 | VERIFIED_FROM_SOURCE |

이미 기록된 결론(VERIFIED_FROM_SOURCE): `docs/history/2026-10-09-local-only-guard.md`가 "버티는 경계는 OS 분리이고 별도 결정"이라고 적고 4단계를 제안했다. ① Claude의 docker·root 작업 목록 ② 그 작업만 허용하는 sudo wrapper ③ docker 그룹 밖 비root로 Claude 실행 ④ 이후 Claude는 직접 배포·상태 복구를 못 한다. `docs/DEPLOYMENT.md`는 2026-08-29 hermes의 docker.sock 마운트를 "host root와 같다"는 이유로 제거한 결정을 적었다.

### 2.3 기존 구성요소 (VERIFIED_FROM_SOURCE, 두 갈래 조사)

| 구성요소 | 무엇을 하나 | 배포 실행기에 쓸 수 있나 | 빠진 것 |
|---|---|---|---|
| `socket_door.py` | 줄 단위 JSON 요청 1개·응답 1개. 소켓 0660·디렉터리 0750, 클라이언트 gid. `SO_PEERCRED` uid 허용 목록(`:459-487`). 동시성 4+4, 프레임 8192바이트, 오류 시 traceback을 응답에서 지우고 `error_ref`만 줌(`:294-321`) | **전송으로 재사용 가능** | uid 검사가 선택 사항(`:473`): 실행기는 `pipeline_worker.py:723`처럼 필수로 강제해야 함. `apply()`에 마감 시간이 없음. 성공 응답 스키마 검증이 없음. 동작 목록은 각 door가 정함 |
| door들(read·switch·dispatch·knowledge) | 닫힌 동작·키 목록, 모르는 키 거부(read는 무시). 셸·경로 인자 없음 | **닫힌 동작 패턴을 재사용** | 호스트에서 서버로 돈 적이 없음(모두 컨테이너). Docker 호출 코드 없음 |
| `bridge_idempotency.py` | `request_id` 지문, claim 15분·완료 24시간, 재사용 거부 | promote·rollback에 재사용 | 효과 도중 충돌 시 TTL 뒤 재실행 가능(자체 명시) |
| R9 승인(`approval.py`, `approval_store.py`, `operator.py`) | Telegram `/approve`: 비공개 채팅, 전달 메시지 거부, 등록된 `from.id`·`chat.id` 대조. 승인은 `action_fingerprint`(스냅샷의 정규 JSON SHA-256)에 결속. 만료, `spend_lock` 아래 CONSUMED 표시. `GOVERNANCE_POLICY.yaml`에 `RELEASE_DEPLOYMENT: 15`분, `approval_reuse_allowed: false`가 이미 있음 | **결속·만료·일회성 의미를 재사용 가능** | 신뢰 기반이 상태 디렉터리의 파일이다(`approvals.jsonl`, `operator_registration.json`). 지금은 root인 Claude가 쓸 수 있다. 배포 동작 유형과 PermissionDecision 생성 경로가 없다. APPROVED 승인을 철회하는 명령이 없다(만료·`/reject`만) |
| Core 승인(`approve_core_release.py`) | 승인자 신원을 CLI 인자로 받음. 커밋 SHA 결속, 만료·소비 없음 | 쓸 수 없음 | — |
| `safety_gate.Authorization` | 환경 변수 기반, 만료 9999-12-31 | 쓸 수 없음(사람 승인이 아님) | — |
| audit 체인(`audit.py`, kernel `integrity`) | 이전 이벤트 SHA를 포함한 해시 체인. 서명 없음 | **증거 기록으로 재사용** | 위조 방지가 아니라 변경 탐지. 실행기 감사는 Claude가 쓸 수 없는 위치에 둬야 함 |

## 3. 위협 모델과 신뢰 경계

### 3.1 보호 대상

| 자산 | 위치 | 왜 중요한가 |
|---|---|---|
| S1 운영 Secret | `/root/thomas_agent/.env`와 백업 사본 3개(이름·모드만 확인, 모두 0600 root) | 실주문 키(`MVP_LIVE_ORDER_API_*`), 계좌 조회 키, 증권사 자격, 봇 토큰, 모델 API 키. compose 변수 이름 기준 |
| S2 실행 중 컨테이너 환경 | 각 컨테이너의 env (Docker API의 `inspect`, `exec`로 보임) | compose가 `environment:`로 넘긴 S1의 일부 |
| S3 holdings LOCAL_ONLY 상태 | 상태 루트의 holdings 디렉터리 | 2026-10-09 위반 사례. 지금은 tripwire만 있음 |
| S4 운영 이미지 태그 | `thomas-agent-runtime:latest`, `rollback-pre-*` | 다음 `compose up`이 무엇을 띄우는지 정함 |
| S5 배포·Rollback 결정권 | Thomas | 승인이 위조되면 S4가 바뀜 |
| S6 런타임 상태 무결성 | `.runtime_governance_state/` (uid 10001) | root 실행이 소유권을 깨뜨린 전례 |

### 3.2 행위자와 신뢰

| 행위자 | 신뢰 |
|---|---|
| Thomas (Mac, SSH 공개키 로그인) | 승인 주체. 유일하게 S5를 가짐 |
| Claude 세션 (데스크톱 앱 SSH 세션, cron이 띄우는 `claude`) | 코드·문서 작성은 신뢰. **S1·S2·S3 접근과 S5는 신뢰하지 않음**. 실수, 프롬프트 주입, 잘못된 추론 모두를 가정 |
| 배포 실행기 (신규, §4.3) | 고정 동작 범위 안에서만 신뢰. S1을 읽는 유일한 비컨테이너 프로세스 |
| 런타임 컨테이너 (uid 10001), hermes (uid 10000) | 기존 경계 유지(`test_deployment_env_passthrough.py`) |
| 호스트 root | 신뢰 기반. 이 설계는 root를 막지 않는다. root는 Thomas만 쓴다 |

### 3.3 위협

| # | 위협 | 지금 | 분리 후 |
|---|---|---|---|
| T1 | Claude가 S1 파일을 직접 읽음 | 막을 수 없음(root) | 파일 모드로 거부 |
| T2 | Claude가 Docker API로 S2를 읽음(`inspect`, `exec env`, `run -v /:/host`) | 막을 수 없음(소켓 소유자 root) | 소켓 그룹 밖이라 연결 거부 |
| T3 | Claude가 디스크에 쓴 스크립트로 Guard를 우회 | 가능(Guard는 명령 텍스트만 봄) | 우회해도 OS가 거부 |
| T4 | Claude가 실행기 코드나 그 checkout을 고쳐 Secret을 출력하게 함 | 해당 없음 | 실행기 코드·트리를 Claude가 쓸 수 없는 위치에 둠(§4.3) |
| T5 | Claude가 승인을 위조하거나 재사용 | 승인 = 대화 텍스트 | 서명 검증 + SHA·이미지 결속 + 만료 + 일회성(§4.4) |
| T6 | 실행기 출력·오류·로그를 통한 누출 | preflight가 출력 억제(행동 통제) | 닫힌 결과 스키마, traceback 차단, 로그는 Claude가 못 읽는 곳 |
| T7 | `/proc/<pid>/environ`·메모리로 compose·실행기 프로세스를 엿봄 | root라 가능 | 다른 uid + `ptrace_scope`=1(호스트 확인)로 거부 |
| T8 | 임시 파일·코어 덤프·셸 history·Claude 대화 기록에 Secret이 남음 | 출력하지 않는 한 남지 않음(행동 통제) | 실행기는 PrivateTmp·코어 덤프 금지. Claude는 애초에 값을 받지 못함 |
| T9 | Claude가 상태 루트를 root로 써서 소유권을 깨뜨림 | 규칙과 `state_guard` | 상태 루트에 쓰기 권한 없음 |
| T10 | 실행기 자체의 결함(임의 인자, 경로 주입) | 해당 없음 | 닫힌 동작·인자 검증·기본 거부, 부정 시험(§4.6) |

남는 위험(분리 후에도): 호스트 root가 뚫리면 전부 뚫린다. 실행기의 Docker 권한은 root와 같다(docker 그룹 = root, `docs/DEPLOYMENT.md` 2026-08-29 기록). 그래서 실행기의 동작 목록과 코드 무결성이 경계의 핵심이다.

### 3.4 신뢰 경계

```
 Thomas (Mac) ──승인(채널 D-2)──▶ ┐
                              │
 Claude 계정 (비root, docker 그룹 밖)
   │  자기 홈의 git clone·worktree만 쓰기
   │  유닉스 소켓 1개로 요청 (SO_PEERCRED uid 허용 목록)
   ▼
 ════════════ 경계 ════════════
 배포 실행기 (D-10: root 권고, 또는 전용 계정 + docker 그룹)
   │  설치본 코드 = root 소유, Claude 쓰기 불가
   │  자기 트리에서 origin/main을 직접 받아 SHA 확인
   ▼
 Docker 데몬 ── 런타임 컨테이너 (uid 10001) / hermes (uid 10000)
```

## 4. 설계

이 절의 계정 이름·uid·경로는 설계 제안이다(DESIGN_ASSUMPTION). 확정은 §8의 결정 항목이다.

### 4.1 C-1 Claude 실행 환경

- **계정:** `claude`(uid 예: 2000). 0·10000·10001이 아니어야 한다. `SO_PEERCRED`는 uid만 보므로, 10000이면 hermes와, 10001이면 런타임과 구별되지 않는다.
- **그룹:** 자기 그룹만. `docker`, `sudo`, `adm`, `systemd-journal`에 넣지 않는다. 어떤 sudoers 파일에도 넣지 않는다.
- **작업 공간:** `/home/claude/work/thomas_agent`에 자기 clone을 두고, worktree도 그 아래에 둔다. `/root`가 0700이라 기존 worktree는 쓸 수 없다. GitHub 접근은 이 계정의 자격(gh 인증, push용 키)으로 한다. 이 자격의 범위는 §8 D-7.
- **설정·기억:** `~/.claude`(memory, skills, projects 기록)를 `/root/.claude`에서 옮긴다. `scripts/ops/harness_backup.sh`는 `/root/.claude/projects`를 백업하므로 경로를 함께 바꾼다.
- **적용 범위:** 데스크톱 앱이 여는 SSH 세션(세션이 SSH로 들어온다는 것은 DESIGN_ASSUMPTION. C1에서 확인), Claude가 띄우는 자동화가 이 계정으로 돈다. root crontab의 "claude" 3줄은 줄마다 정한다(§4.9). Secret을 읽는 스크립트는 이 계정으로 옮기지 않는다(W-26). Thomas 본인의 root SSH 로그인은 그대로 둔다.
- **전환 절차 (중단 없이):**
  1. 계정과 작업 공간을 만들고 clone을 받는다. 기존 세션·컨테이너는 건드리지 않는다.
  2. 새 계정으로 시험 세션 하나를 열어 §4.6 부정 시험을 돌린다(C4·C6).
  3. 데스크톱 앱의 SSH 대상 사용자를 `claude`로 바꾼다. Mac 쪽 설정이며 Thomas가 한다. cron 줄은 §4.9에서 Claude 작업으로 확인된 것만 `claude` 계정 crontab으로 옮긴다.
  4. root로 도는 Claude 프로세스가 끝나기를 기다린다. 강제 종료하지 않는다. 남은 root 세션은 `ps -eo user,comm`으로 센다.
  5. 이후 root 세션 사용을 금지하는 규칙을 CLAUDE.md에 넣는다. 거버넌스 변경이다.
- **잃는 것과 대체 경로:** C1 인벤토리(§4.7)가 작업 30개를 행 단위로 정한다. 요약하면 다음과 같다.
  - 배포·candidate 작업은 실행기로 옮긴다(C).
  - 읽기 일부는 read-bridge로 대체한다(B). 단, 접근 전제(§4.8)가 먼저 풀려야 한다.
  - 상태 쓰기·돈 경로·상태 복구는 Claude가 하지 않는다(D).
  - 저장소 작업과 블로그 자동화는 새 계정에서 그대로 한다(A).
  - holdings LOCAL_ONLY는 이로써 실제 경계가 된다.

### 4.2 C-2 Secret 접근 경계

- **실행기가 `.env`와 상태 파일에 닿는 방법은 미결정이다(D-10).** `/root`가 0700이고 상태 루트가 0700 uid 10001이므로, 비root 실행기 계정은 `.env` 모드를 0640으로 바꿔도 경로를 지나가지 못한다. 그러면 `compose config`가 env 파일을 못 읽고, scheduler 파일 읽기는 `fires` STOP이 된다. 선택지:
  - **E1 실행기를 root로 실행:** docker 그룹은 이미 root와 같으므로 잃는 격리는 거의 없다. 전용 계정이 주는 것은 감사 구분뿐이다. `.env`·상태 경로를 바꾸지 않는다. **권고 기본값**
  - **E2 `thomas-deploy` 계정 + ACL:** `/root`와 `/root/thomas_agent`에 지나가기(x)만, `.env`에 읽기, 상태 루트의 scheduler 파일 두 개에 읽기 ACL. 호스트 권한 변경이 늘고 ACL 관리가 필요하다.
  - **E3 `thomas-deploy` 계정 + 상태 읽기를 컨테이너 경유:** scheduler 파일은 `docker exec thomas-scheduler`(uid 10001)로 읽고, `.env`만 E2처럼 연다. 상태 루트 권한은 그대로다.
- 어느 경우든 Claude 계정은 `/root` 아래 어디에도 접근하지 못한다. 그것이 C-2의 경계다.
- 백업 사본 3개는 실행기도 읽을 필요가 없다. 호스트 밖 보관이나 삭제를 Thomas가 정한다(§8 D-6).
- **우회 노출 경로와 대응:**

| 경로 | 대응 | 근거 |
|---|---|---|
| 파일 직접 읽기 | `/root` 0700과 `.env` 0600. Claude 계정은 경로를 지나가지도 못함 | 파일 권한 |
| Docker API(`inspect` env, `exec`, `run -v /:/host`) | Claude는 docker 그룹 밖. 소켓 0660 | 파일 권한 |
| `/proc/<pid>/environ`·`mem`(compose, 실행기) | 다른 uid, `ptrace_scope`=1 | 커널 |
| 실행기 응답 | 닫힌 스키마: `level`, `check`, `reason_code`, 이미지 id, SHA. 자유 텍스트는 고정 문구 목록에서만. compose 출력·env·traceback은 넣지 않음 | 실행기 코드(§4.3) |
| 실행기 오류 | `socket_door`처럼 traceback은 응답에서 빼고 `error_ref`만 준다. traceback은 실행기 로그(Claude가 못 읽는 곳)에만 남긴다 | 재사용 |
| 실행기 로그·journal | `/var/log/thomas-deploy`(0750, 실행기 계정). Claude는 `adm`·`systemd-journal` 밖 | 파일 권한 |
| 임시 파일 | 실행기는 systemd `PrivateTmp=yes`. compose 출력을 파일로 쓰지 않음 | systemd |
| 코어 덤프 | `LimitCORE=0` | systemd |
| Claude 대화 기록·백업 | Claude가 값을 받지 못하므로 남을 값이 없음 | 위 전부 |
| git 커밋 | `.env*`는 `.gitignore`·`.dockerignore` 대상(기존). Claude clone에는 `.env`가 없음 | 기존 + 분리 |

출력 억제는 이 표에서 독립된 방어로 세지 않는다. 모든 행은 OS가 거부하거나, Claude에게 값이 도달하지 않는 구조다.

### 4.3 C-3 제한된 배포 실행기

**기존 구성요소로 충분한가:** 충분하지 않다. 근거는 세 가지다.
1. 호스트에서 Docker를 호출하는 서버가 저장소에 없다. door는 모두 컨테이너 안에서 돈다.
2. sudo wrapper(2026-10-09 안)는 인자 검증·승인 확인·감사를 셸 스크립트로 다시 짜야 한다. sudoers 규칙은 인자 패턴에 약하다.
3. 배포 승인 동작 유형이 없다.

그래서 새로 만드는 것은 **실행기 프로세스 하나와 그 동작 목록**으로 한정한다. 전송·감사·승인 의미·preflight 판정은 재사용한다(§5).

- **형태:** 호스트 systemd 서비스 `thomas-deploy-runner`. 실행 계정은 D-10(E1 root 권고, E2·E3 `thomas-deploy`). docker 그룹은 root와 같다는 점(§3.3)을 받아들이고, 경계는 동작 목록과 코드 무결성에 둔다.
- **전송:** `socket_door.SocketDoor`. 소켓은 `/run/thomas-deploy/runner.sock`(디렉터리 0750, 그룹 `claude`). `bridge/`가 아니다. `bridge/`는 hermes에 마운트된다. uid 허용 목록은 `{claude uid}` 하나이고 필수다. 비어 있으면 listen을 거부한다(`pipeline_worker.py:723` 방식).
- **코드 위치:** `/opt/thomas-deploy/<sha>/`(root 소유 0755, Claude 쓰기 불가). 설치는 Thomas가 검토한 origin/main SHA를 복사하는 행위이고, Thomas의 작업이다. 실행기는 설치본에서만 import한다.
- **자기 트리:** 실행기는 `/var/lib/thomas-deploy/trees/<N>`에 자기 clone을 둔다. GitHub에서 직접 fetch하고, 승인이 결속한 SHA와 HEAD를 비교한다. **이유:** 지금 `check_tree`는 트리 자신의 `rev-parse origin/main`을 믿는다(`deploy_preflight.py:225`). Claude가 소유한 트리라면 Claude가 `.git/refs/remotes/origin/main`과 빌드 컨텍스트를 바꿀 수 있다. 실행기가 Claude의 worktree를 빌드하면 경계가 사라진다(T4).
- **동작 목록 (제안, 권한 부여 아님):**

| 동작 | 하는 일 | Docker 효과 | 승인 |
|---|---|---|---|
| `inspect_candidate(pr)` | 실행 중 이미지 id, `candidate-<pr>`·`rollback-pre-<pr>`·`latest` id, `thomas-` 컨테이너 이름·health. env·Mounts는 돌려주지 않음 | 읽기 | 없음(메타데이터 읽기) — §8 D-4 |
| `prepare_candidate(pr)` | 자기 트리 측정(clean, HEAD = `origin/main`), 실행 중 이미지 측정(여덟 서비스가 한 이미지, compose project 라벨 대조), 태그 비어 있음 확인, 측정한 실행 중 이미지 id로 `rollback-pre-<pr>` 태그, `candidate-<pr>` 빌드. compose config(bind source 검사)는 `validate_candidate`로 옮긴다(2026-10-16 정정, §4.25.3) | 태그·빌드 | 필요 |
| `validate_candidate(pr)` | D-10 격리로 `c7_blocked_selftest` 같은 등록된 자가검증 실행, `--promote --tree` preflight를 실행기 안에서 실행. 판정 줄만 돌려줌 | 읽기 + 격리 실행 | 필요(Secret을 실행기 안에서 처리하므로) |
| `promote(pr, sha, candidate_id, rollback_id, request_id)` | 직전 preflight 재실행, STOP 0이면 `latest` 태그 + `compose up -d`, 사후 확인 | 운영 변경 | 필요(결속) |
| `rollback(pr, rollback_id, request_id)` | `rollback-pre-<pr>`를 `latest`로, 같은 compose, 사후 확인 | 운영 변경 | 필요(결속) |

- **원칙:** 셸 없이 argv만 사용한다(`pdf_text.py`의 `shutil.which`·고정 argv·최소 env 패턴). 경로 인자는 받지 않는다. 트리 경로는 PR 번호에서 실행기가 만든다. 동작별로 키 목록이 닫혀 있고, 모르는 키는 거부한다(`ARGUMENT_NOT_ACCEPTED`). 모르는 동작도 거부한다(`VERB_NOT_PERMITTED`). `promote`·`rollback`은 `request_id`가 필수다(`bridge_idempotency` 재사용). `apply`에는 실행기 쪽 마감 시간을 둔다. 모든 실패는 STOP이고, 다시 시도하지 않는다.
- **결과 스키마:** 닫힌 JSON Schema(`additionalProperties: false`)를 응답 직전에 검증한다. 검증에 실패하면 응답 대신 `REPLY_SCHEMA_INVALID`를 준다. 이것은 새 스키마이고 Reuse-first 예외 승인이 필요하다(§8 D-3).

### 4.4 C-4 승인 검증

필수 조건: 승인자 인증, 동작·대상 식별, SHA·candidate id·rollback id 결속, 프로젝트·환경 결속(`thomas_agent`, `/root/thomas_agent/.env`), 만료, 재사용 방지, 취소, 검증 실패 시 거부.

| 선택지 | 승인자 인증 | 결속 | 만료·일회성 | 취소 | 위조 저항 | 새로 만들 것 |
|---|---|---|---|---|---|---|
| **R1 기존 R9 Telegram `/approve` 재사용** | Thomas의 Telegram 계정(등록 id 대조) | `action_fingerprint`에 SHA·이미지 id·프로젝트를 넣음 | 있음(`RELEASE_DEPLOYMENT` 15분, `spend_lock` 소비) | 만료·`/reject`만. APPROVED 철회 없음 | **분리 후 Claude는 못 함**. 전제: Claude가 상태 루트에 쓸 수 없고 봇 토큰도 없다. root·uid 10001 프로세스는 여전히 쓸 수 있음 | 배포 동작 유형 1개, PermissionDecision 생성 경로, 실행기의 소비 호출 |
| **R2 Thomas SSH 서명** (`ssh-keygen -Y sign`/`verify`) | Thomas의 Mac 개인키. 호스트에는 공개키만 있음 | 서명 대상이 결속 문서(동작, PR, SHA, 이미지 id, 프로젝트, 만료, nonce) | 만료는 문서 안에. 일회성은 실행기의 nonce 소비 기록 | 실행기의 철회 목록 파일(root 소유) | **서명은 호스트 root도 위조 못 함.** 그러나 root는 검증기·허용 서명자 파일을 바꾸거나 Docker를 직접 불러 승인 자체를 건너뛸 수 있다(§4.10) | 결속 문서 형식, 검증·nonce 소비 코드. 운영자 도구(Mac 명령) |
| R3 Thomas가 root SSH로 승인 파일 작성 | root 권한 | 파일 내용 | 실행기 구현 | 파일 삭제 | 분리 후 Claude는 못 함. root는 위조 가능 | 파일 형식 |

- R1의 소비는 `approvals.jsonl`에 쓴다. CLAUDE.md는 상태 파일을 호스트 root로 쓰는 것을 금지한다. 그래서 실행기는 `docker exec thomas-operator python -m runtime.mvp_runtime.approval_cli consume …`처럼 uid 10001 컨테이너를 거쳐 소비해야 한다(DESIGN_ASSUMPTION. 정확한 CLI 인자는 C2에서 확인).
- **승인 채널은 미결정이다(§8 D-2).** 정해지기 전에는 `promote`·`rollback`을 구현하지 않는다. 구현해도 승인 검증이 없으면 항상 거부하는 상태로 둔다.
- 대화 안의 Thomas 문구는 어느 선택지에서도 승인이 아니다.
- 어느 선택지든 "승인 없이는 배포되지 않는다"는 보장은 root가 아닌 행위자에게만 성립한다. 그 보장을 실제로 만드는 것은 서명 방식이 아니라 실행기·검증 설정·Docker에 대한 파일 권한이다(§4.10).

### 4.5 C-5 감사 기록

- 실행기는 `/var/lib/thomas-deploy/audit.jsonl`에 해시 체인으로 쓴다. kernel `integrity` 해시를 재사용하고, 쓰기 권한은 실행기 계정만 갖는다. Claude 읽기 허용 여부는 §8 D-5에서 정한다(권고: 허용, 메타데이터뿐이므로).
- **필드:** `requested_by_uid`, `verb`, `approval_ref`, `pr`, `sha`, `candidate_image_id`, `rollback_image_id`, `started_at`, `ended_at`, `outcome`(PASS/STOP/ERROR), `reason_codes`(닫힌 목록), `previous_event_sha256`.
- **기록하지 않는 것:** Secret 값, compose config 전체, env 전체, 명령 출력 원문.
- 해시 체인은 변경을 탐지할 뿐, 행위자를 증명하지 않는다. 요청자는 `SO_PEERCRED` uid로, 승인 출처는 R2를 고르면 서명으로 기록된다. 감사 파일은 root가 다시 쓸 수 있으므로(체인 재계산), root 침해 뒤의 감사 기록은 증거가 되지 못한다.

### 4.6 C-6 분리 검증 (부정 시험 명세)

각 시험의 결과는 PASS / FAIL / NOT_TESTED / BLOCKED 중 하나다. **운영 호스트 기준으로는 모두 NOT_TESTED다.** 호스트 시험(N-1–N-6)은 계정 분리 후 `claude` 계정으로 실행하고(C4·C6), 실행기 시험(N-7–N-12)은 합성 fixture로 먼저 실행한다(C3). 실행기 설치 시험(N-13·N-14, 아래 C1 추가분)은 C4에서 시작해 C6에서 운영 호스트로 반복하고, N-15는 관리 설정 위치(D-13)가 정해진 뒤 C6에서 실행한다(2026-10-16 번호 정정). 2026-10-16 현재 C3a 합성 시험이 `prepare_candidate` 기준으로 N-7–N-12를 다룬다(§4.25.2). `promote`·`rollback`은 구현되지 않아 그 경로의 N-8–N-11은 NOT_TESTED다.

| # | 시험 | 방법 | PASS 조건 | BLOCKED 조건 |
|---|---|---|---|---|
| N-1 | Secret 파일 읽기 거부 | `claude`로 `os.access(path, R_OK)`와 `open()` 시도. `open()`이 성공하면 읽지 않고 즉시 닫고 FAIL로 기록. 예외 종류만 기록 | `PermissionError`. `.env`와 사본 모두 | 계정이 아직 없음 |
| N-2 | Docker 소켓 거부 | `claude`로 소켓 `connect()`만 시도. API 요청은 보내지 않음 | `PermissionError` | 같음 |
| N-3 | sudo 거부 | `sudo -n true` | 0이 아닌 종료, 비밀번호 요구 없이 거부 | 같음 |
| N-4 | 실행기 코드·트리·감사 수정 거부 | `/opt/thomas-deploy`, `/var/lib/thomas-deploy/trees`, `audit.jsonl`에 쓰기 시도 | 모두 `PermissionError` | 실행기 미설치 |
| N-5 | 실행기 프로세스 접근 거부 | 실행기 pid의 `/proc/<pid>/environ` 읽기, `ptrace` attach | 모두 거부 | 같음 |
| N-6 | 상태 루트 쓰기·holdings 읽기 거부 | 상태 루트에 쓰기, holdings 디렉터리 `os.access` | 거부 | 계정이 아직 없음 |
| N-7 | 임의 동작·키·경로 거부 | `{"verb":"exec"}`, `promote`에 `tree` 키, PR에 경로 문자열 | `VERB_NOT_PERMITTED`, `ARGUMENT_NOT_ACCEPTED` | — |
| N-8 | 승인 없는 promote 거부 | 승인 참조 없이 `promote` | 승인 거부 reason code, Docker 호출 0회 | 승인 채널 미결정 |
| N-9 | 만료된 승인 거부 | 만료 시각이 지난 승인 | 만료 reason code, Docker 호출 0회 | 같음 |
| N-10 | 다른 SHA·이미지 id 승인 재사용 거부 | 결속 값 하나를 바꾼 요청 | 결속 불일치 reason code, Docker 호출 0회 | 같음 |
| N-11 | 승인 재사용 거부 | 같은 승인으로 두 번째 `promote` | 이미 소비됨 reason code | 같음 |
| N-12 | 실패 시 기본 거부 | Docker 명령 실패, 시간 초과, 응답 스키마 위반, 승인 저장소 읽기 실패를 각각 주입 | 모두 STOP·ERROR, 다음 단계 명령 0회, 응답에 원문 없음 | — |

합성 fixture는 기존 preflight 테스트 방식을 따른다. 명령 실행기 `run`을 가짜로 주입하고, 합성 `.env` 값이 응답·로그·예외에 나타나지 않는지 검사한다(`test_ops_deploy_preflight.py`의 누출 검사와 같은 방식).

C1에서 더한 시험(§4.10):

| # | 시험 | 방법 | PASS 조건 | BLOCKED 조건 |
|---|---|---|---|---|
| N-13 | 실행기 import 경로 오염 거부 | `claude`가 쓸 수 있는 디렉터리에 같은 이름의 모듈을 두고, 실행기 서비스의 `PYTHONPATH`·작업 디렉터리·`sys.path`를 확인 | 실행기가 설치본 밖에서 아무것도 import하지 않음(`python -I`, 고정 작업 디렉터리) | 실행기 미설치 |
| N-14 | 검증 설정 수정 거부 | 허용 서명자 파일, 철회 목록, nonce 기록, systemd unit에 쓰기 시도 | 모두 `PermissionError` | 같음 |
| N-15 | Claude 설정의 Guard 무력화 | `claude` 계정으로 관리 설정 파일(§4.10) 쓰기 시도 | 거부 | 관리 설정 위치 미확인 |

### 4.7 C1: Claude 작업 의존성 인벤토리 (2026-10-13)

**출처와 근거 등급**
- 저장소 출처(CLAUDE.md, `.claude/skills`, runbook, `scripts/ops`)는 origin/main `1b45ed57`의 파일을 읽어 줄 번호를 확인했다(VERIFIED_FROM_SOURCE).
- `/root/.claude/scripts`는 이번 확인 범위가 아래와 같다.
  - 파일 이름 목록을 봤다.
  - docker·chown·sudo·`.env`·상태 루트를 언급하는 파일을 찾았고, 그 줄만 봤다.
  - `/root/.claude/settings.json`에서는 hook 이름과 스크립트 경로만 뽑았다.
- cron 줄과 Mac 자동화는 이전 세션 기록에만 근거한다(REPORTED_BUT_NOT_RECHECKED). crontab은 다시 읽지 않았다(§4.9).
- 어느 명령도 이번에 실행하지 않았다.

**분류:** A 비root 계정에서 직접 / B 기존 read-bridge / C 새 제한 인터페이스(실행기) / D 분리 후 Claude가 하지 않음 / E 추가 근거 필요. D 중 "선행 변경"이 적힌 행(W-30)은 Claude가 하지 않지만 분리 전에 다른 변경이 필요하다는 뜻이다(§4.12.4)

| ID | 작업 | 출처 | 지금 필요한 권한 | 읽기/변경 | Secret·런타임 데이터 | 비root 가능 | 권장 대체 | 기존 구현 | 미해결 |
|---|---|---|---|---|---|---|---|---|---|
| W-01 | 첫 preflight | deploy `SKILL.md` 2단계; `deploy_preflight.py` | root(Docker API, 상태 루트 읽기), gh | 읽기 | 런타임(scheduler 파일 2개). compose config는 실행하지 않음 | 불가 | **C** `prepare_candidate` 안 | 판정 로직만 재사용 | D-10 |
| W-02 | rollback 태그, candidate 빌드 | `SKILL.md:20-21` | Docker | 변경(이미지 저장소) | 없음 | 불가 | **C** `prepare_candidate` | 없음 | D-1, D-3 |
| W-03 | 이미지 안 assert, D-10 격리 자가검증 | `SKILL.md:23`; 검증 설계 문서 §2 | Docker(`run`) | 읽기(일회용 컨테이너) | 없음(격리) | 불가 | **C** `validate_candidate`는 등록된 자가검증만 실행. PR마다 다른 `python -c` assert는 **D** | 진입점 `c7_blocked_selftest.py` 재사용 | 임의 assert를 잃는다. 자가검증 목록을 어디에 둘지 |
| W-04 | promote 직전 preflight(`--promote --tree`) | `SKILL.md` 6단계 | root(`.env`, Docker) | 읽기 | **Secret**(compose config) + 런타임 | 불가 | **C** `validate_candidate`·`promote` 안 | 없음 | D-2, D-10 |
| W-05 | promote + `compose up -d` | `SKILL.md:33` | root(`.env`, Docker) | 변경(운영) | **Secret** | 불가 | **C** `promote` | 없음 | D-2 |
| W-06 | 배포 후 확인: 실행 이미지, health, 다음 `fired` | `SKILL.md:35-41` | Docker, 상태 읽기 | 읽기 | 런타임 | 불가 | 이미지·health는 **C**(`promote` 사후 확인, `inspect_candidate`). `fired` 이벤트는 **B** `scheduler_events` | 부분(B) | §4.8 접근 전제 |
| W-07 | Rollback | `SKILL.md:48` | root(`.env`, Docker) | 변경(운영) | **Secret** | 불가 | **C** `rollback` | 없음 | D-2, Rollback 권한 |
| W-08 | 정리: worktree·orphan candidate 제거 | `SKILL.md` 9단계 | git, Docker(`rmi`) | 변경 | 없음 | worktree는 가능, 이미지는 불가 | Claude worktree는 **A**. 실행기 트리·이미지는 실행기가 스스로 정리(**C**) | 없음 | 정리를 별도 동작으로 둘지 |
| W-09 | one-off 스크립트 `tool-<N>`(상태 rw 마운트) | `SKILL.md:52-66`; CLAUDE.md:160 | Docker, 상태 쓰기 | 변경 가능 | 런타임 | 불가 | **D**(Thomas). 임의 스크립트는 닫힌 동작이 될 수 없다 | — | — |
| W-10 | 상태 쓰기 CLI `docker exec thomas-scheduler python -m scripts.…` | CLAUDE.md:97-103 | Docker | 변경 | 런타임. 실행 컨테이너에 Secret env | 불가 | **D**(Thomas). 자주 쓰는 CLI가 있으면 개별 door를 따로 제안(**E**) | — | 어떤 CLI를 얼마나 쓰는지 기록 없음 |
| W-11 | MVP CLI intake | CLAUDE.md:172; verify `SKILL.md:119` | Docker | 변경(원장) | 런타임, 모델 API 키 env | 불가 | **E** 유지: dispatch door는 요청 텍스트·kind 4종·`naver_keywords`를 표현한다. 그러나 요청자 기록, 검증 정책, 기능 범위, door 접근 범위가 다르다(§4.8.2) | 부분 | D-14(권고 D, 필요하면 C) |
| W-12 | `--naver-keywords` 실행(pipeline-worker) | CLAUDE.md:186-192 | Docker | 변경 | Naver 키 env | 불가 | **E** 유지: W-11의 차이에 더해, seed 길이를 door에서 먼저 거부한다. Naver env를 가진 실행 위치는 같은 pipeline-worker다(§4.8.2) | 부분 | 같음 |
| W-13 | `live_readiness` | CLAUDE.md:209; verify `SKILL.md:145`; `RUNBOOK_CONSECUTIVE_LOSS_BREAKER.md:63` | Docker | 읽기 | 런타임 + scheduler 프로세스 env 기반 판정 | 불가 | **E** 유지: 같은 보드가 세 가지 다른 답을 낸다(§4.8.3). `live_entry_possible`은 read-bridge에서 기록 기반으로 부분 동등이고, 프로세스 `ready`는 비동등이다 | 부분 | D-14 |
| W-14 | `register_execution_stage --show` | CLAUDE.md:210; `RUNBOOK_HARNESS_BACKUP_RESTORE.md:196` | Docker | 읽기 | 런타임 | 불가 | 판정 목적은 **B** `crypto_readiness`의 `data.execution_stage`(`crypto_status`에는 단계가 없다). 원장·anchor 세부와 실행 중 정책 해시, `--sync-anchor`는 **D**(Thomas). §4.8.1 | 있음(부분) | §4.8 접근 전제, 이미지 동일성 |
| W-15 | `promote_strategy_candidates --list` | verify `SKILL.md:146` | Docker | 읽기 | 런타임 | 불가 | **D**(권고): `crypto_status`는 후보 집계만 주고 후보별 행은 없다. 후보별 목록을 Claude가 읽어야 한다면 read-bridge에 새 읽기를 더하는 **C**가 필요하다(D-15). §4.8.1 | 없음 | D-15 |
| W-16 | `holdings_board` / `--json` | CLAUDE.md:62-63 | Docker | 읽기 | 런타임(밖으로 나가도 되는 집계 한 표) | 불가 | **B** `holdings_status` | 있음 | §4.8 접근 전제 |
| W-17 | scheduler 상태 읽기(`schedules`, scheduler 이벤트) | 세션 관행; `deploy_preflight.py:322-330` | root 파일 읽기 | 읽기 | 런타임 | 불가 | Claude의 조회는 **B** `schedules`·`scheduler_events`. 실행기 preflight는 B를 쓰지 않는다(D-10) | 있음 | `scheduler_events`는 최근 N개만 |
| W-18 | 콘솔 상태, `operator_control_state.json` 원문 | `RUNBOOK_CONSECUTIVE_LOSS_BREAKER.md:67`; `RUNBOOK_APPROVAL_PATH_DRILL.md:52` | Docker | 읽기 | 런타임 | 불가 | **B** `runtime_status`(같은 `control.CMD_STATUS`). 원문 `cat`은 대체하지 않는다(**D**) | 있음 | — |
| W-19 | `halt_trading`, `kill`, 위험 한도 등록·삭제 | `RUNBOOK_CONSECUTIVE_LOSS_BREAKER.md:173-185,232-233` | Docker | 변경(돈 경로) | 런타임 | 불가 | **D**: Claude는 돈 경로를 만지지 않는다(CLAUDE.md). Thomas 또는 운영 채널 | — | — |
| W-20 | `docker logs`: 기동 설정 배너, 일반 관찰, 사고 진단(사용처 8곳) | `DEPLOYMENT.md:186,208,556,618`; `RUNBOOK_APPROVAL_PATH_DRILL.md:86`; `RUNBOOK_HARNESS_BACKUP_RESTORE.md:311`; `socket_door.py:294-304`; `health_watch.sh:365`; `scheduler.py:2640-2655` | Docker | 읽기 | 로그에 Secret이 없다는 증거 없음 | 불가 | 사용처별로 나눈다(§4.8.4): 관찰·deferral 빈도는 **B**, 설정 배너는 **C**(실행기 사후 확인의 고정 패턴), 사고 stack·door traceback·운영자 알림은 **D**. 원문 로그는 어떤 경우에도 Claude에게 주지 않는다 | 부분(B) | D-16 |
| W-21 | `chown -R 10001:10001` 상태 복구 | CLAUDE.md:103; verify `SKILL.md:78`; `RUNBOOK_HARNESS_BACKUP_RESTORE.md:117`; `RUNBOOK_WORKFLOW_CUTOVER.md:113` | root | 변경 | 런타임 | 불가 | **D**(Thomas). 원인인 root 실행이 분리 후 사라진다 | — | — |
| W-22 | 테스트용 Core 활성화 | verify `SKILL.md:72-96`; `test_run.sh:9,89-91` | 자기 clone 쓰기. verify의 shortcut은 `chown` 포함 | 변경(로컬) | 없음 | `test_run.sh` 경로는 `--allow-foreign-root-run` 없이 활성화한다. 비root에서 `chown`이 필요 없다는 것은 DESIGN_ASSUMPTION | **A** | 있음 | 비root에서 skip 수 변화(`skip_ceiling.json`). C4에서 확인 |
| W-23 | hermes 백업·복구, shim 설치 | `RUNBOOK_HARNESS_BACKUP_RESTORE.md:55,156-200`; `install_hermes_shims.sh:63` | root, Docker, `chown 10000` | 변경 | hermes 데이터 | 불가 | **D**(Thomas) | — | — |
| W-24 | workflow cutover | `RUNBOOK_WORKFLOW_CUTOVER.md:35-113` | Docker(`compose`, `exec`, `run`), `chown` | 변경 | 런타임 | 불가 | **D**(Thomas) | — | — |
| W-25 | SessionStart hook의 호스트 요약 | `/root/.claude/settings.json`(SessionStart); `session-start-check.sh:38-41` | Docker API 읽기 | 읽기 | 메타데이터 | docker 줄만 실패한다(스크립트는 실패를 허용, `:2`) | docker 요약은 빼고, 실행기가 생기면 **C** `inspect_candidate` | 없음 | 경로 `/root/.claude` → 새 홈 |
| W-26 | `hermes-notify.sh`: Mac 자동화 결과를 Hermes 봇으로 발신 | `/root/.claude/scripts/hermes-notify.sh:2-9` | root(`.env`, 상태 루트 읽기) | 외부 발신 | **Secret**(`HERMES_BOT_TOKEN`) + 런타임(`operator_registration.json`) | 불가 | **D**(Claude 쪽): Claude가 쓸 수 없는 root 소유 위치로 옮기고 Mac 자동화 전용으로 둔다 | — | **지금 Claude가 고칠 수 있는 스크립트가 Secret을 읽는다(§4.9).** Mac이 어느 계정으로 SSH하는지 NOT_VERIFIED |
| W-27 | `blog-daily-draft.sh`(headless Claude, 블로그 초안) | 이전 기록: root cron `2 23 * * *`(REPORTED); `/root/.claude/scripts/blog-daily-draft.sh`(파일 존재 확인) | root 홈의 Claude 자격, 볼트 쓰기, scripts·skills git 커밋 | 변경(볼트, 로컬 git) | Claude 구독 자격(`~/.claude`) | 이전 후 가능 | **A**(`claude` 계정 crontab) | 있음 | cron 줄 NOT_VERIFIED. 볼트 `/root/obsidian-thomas`의 소유권 |
| W-28 | Claude 사용자 hook: `vault-autopush`(Stop), `vault-guard`(PostToolUse), `pgrep-wait-guard`·전역 `guard-local-only`(PreToolUse) | `/root/.claude/settings.json` 등록 이름 | 볼트 쓰기·push | 변경 | 볼트 push 자격(위치 미확인) | 이전 후 가능 | **A** | 있음 | push 자격 위치 NOT_VERIFIED. 설정 무결성(§4.10) |
| W-29 | gh/git: PR, 병합 | 세션 관행 | GitHub 자격 | 변경(원격) | gh 토큰 | 가능 | **A** | 있음 | D-7: 병합 권한(§4.10) |
| W-30 | `harness_backup.sh transcripts`가 Claude 대화 기록을 백업 | `harness_backup.sh:15,57,94-115` | root(root cron) | 읽기 | 대화 기록 | 해당 없음(root 작업으로 남음) | **D**(Claude가 하지 않음, root cron으로 남음). 단 분리 전에 스크립트에 Claude 홈용 두 번째 루트 변수가 필요하다. 없으면 백업이 조용히 빠진다(§4.12.3). C5 점검 항목이자 C6 중단 조건 | 부분 | 스크립트 변경(승인 필요), 설치본 내용 NOT_VERIFIED |

**root로 남는 호스트 자동화 (Claude가 아님):** 저장소가 정의한 root cron이다. Claude 계정으로 옮기지 않는다.
- `health_watch.sh`: 10분마다, `docker inspect`·`ps`·`systemctl` 사용(`:2-9`)
- `backup_watch.sh`: 매일 08:00Z(`:6`)
- `harness_backup.sh`: core 07:45Z, transcripts 07:55Z(`:7,15`)
- `rotate_hermes_mcp_log.sh`: 07:40Z(`:8`)

**집계**

| 분류 | 작업 |
|---|---|
| A | W-08 일부, W-22, W-27, W-28, W-29 |
| B | W-06 일부, W-14 일부, W-16, W-17, W-18, W-20 일부 |
| C | W-01–W-07(W-03 일부), W-20 일부 |
| D | W-03 일부, W-09, W-10, W-14 일부, W-15, W-18 원문 읽기, W-19, W-20 일부, W-21, W-23, W-24, W-26, W-30 |
| E | W-11–W-13 |

대체 경로가 없는 필수 작업은 찾지 못했다. 다만 E 3건은 Thomas 판단이나 추가 근거가 필요하다(W-30은 §4.12.3에서 D로 옮겼다. 2026-10-13 W-14·W-15 소스 대조로 7건에서 5건, W-20 사용처별 분류로 4건이 됐다, §4.8.1, §4.8.4).

### 4.8 C1: read-bridge 대조

**read-bridge의 실제 읽기 목록:** `runtime/mvp_runtime/read_bridge.py:63-91`의 `_READS` 15개다. 정책 1.6.2의 `control_channel.assistant_read.verbs`가 15개 모두를 허가한다(`GOVERNANCE_POLICY.yaml:352-371`). `lane_digest`·`holdings_status`는 정책 게이트를 거치는데(`POLICY_GATED_READS`), 둘 다 정책에 들어 있다.

| Claude 작업 | read-bridge 읽기 | 같은 출력·범위인가 | 판정 |
|---|---|---|---|
| W-16 `holdings_board --json` | `holdings_status` | 같다. 둘 다 `holdings.store.load_holdings_view`를 부른다(`holdings_board.py:214-219`, `domain_console.py:154-162`). 정책 주석: 집계뿐, 종목·종목별 수치 없음 | **B** |
| W-17 scheduler 상태 | `schedules`, `scheduler_events` | 행과 최근 이벤트. `scheduler_events`는 최근 N개(`store_reads.py:103-126`). 원본 파일 전체는 아니다 | **B**(조회 목적). 실행기 preflight에는 쓰지 않는다 |
| W-18 콘솔 상태 | `runtime_status` | 같은 `control.CMD_STATUS`(`read_bridge.py:64`) | **B**. 상태 파일 원문은 D |
| W-06 다음 `fired` 확인 | `scheduler_events` | 이벤트 종류와 시각이 있으면 충분 | **B** |
| W-13 `live_readiness` | `crypto_readiness` | **같지 않다.** 같은 `live_readiness.build_readiness`를 부르지만(`domain_console.py:114-120`), 이 보드는 실행 프로세스의 env로 READY를 판정한다(`live_readiness.py:12-20`). read-bridge 컨테이너에는 crypto env가 없으므로 READY는 scheduler의 판정이 아니다. 진입 가능 여부(YES/NO/UNKNOWN)는 scheduler가 기록한 상태에서 읽는다(§4.8.3) | **E**. `ready` 비동등, `live_entry_possible` 부분 동등, `execution_stage` 동등. 기록된 상태로 충분한지 Thomas 판단 |
| W-14 실행 단계 | `crypto_readiness`(`data.execution_stage`) | 판정은 같다. 세부 진단은 없다(§4.8.1) | **B**(판정) / **D**(진단) |
| W-15 전략 후보 목록 | 없음. `crypto_status`는 집계뿐 | 다르다(§4.8.1) | **D**(권고), C는 D-15 |

**접근 전제 (B를 쓰기 전에 풀어야 함):**
1. **소켓에 닿을 수 없다.** 소켓은 상태 루트 아래 `bridge/read.sock`이고(`read_bridge.py:50`), 상태 루트는 `/root`(0700) 안의 0700 uid 10001 디렉터리다. hermes는 bind mount로 닿지만, 호스트의 `claude` 계정은 경로를 지나가지 못한다.
2. **허용 목록에 없다.** 클라이언트 uid·gid의 기본값이 10000이다(`docker-compose.yml:467-469,488`). uid 0도 의도적으로 뺐다.
3. **풀 방법(미결정, D-11):**
   - **(a)는 2026-10-13 권고에서 철회했다(§4.12.1).** 허용 목록 변수 하나가 read·dispatch·knowledge·switch 네 door에 함께 들어가므로, Claude uid를 넣으면 네 door가 모두 열린다. 원래 문안: (a) 호스트에서 `bridge/` 디렉터리를 `/run/thomas-bridge`로 bind mount하고, `claude` uid를 허용 목록에 더한다. 허용 목록은 쉼표로 여러 uid를 받는다(`socket_door.py:569-590`). bind mount는 같은 inode를 같은 모드로 보여 주므로, 소켓 0660과 디렉터리 0750의 그룹(클라이언트 gid, 기본 10000, `socket_door.py:665-698`)에도 `claude`가 들어가야 한다. 방법은 gid 10000 그룹에 넣는 것과 `MVP_BRIDGE_CLIENT_GID`를 바꾸는 것이다. 후자는 hermes 접근에도 영향을 준다.
   - (b) `/root`와 상태 루트에 지나가기(x) ACL을 준다. 상태 루트를 지나갈 수 있게 되면 이름을 아는 다른 파일의 모드가 마지막 방어가 된다. 권고하지 않는다.
   - (c) B를 포기하고 해당 읽기를 Thomas에게 맡긴다.
   - (d) Claude 전용 read-bridge 인스턴스(2026-10-13 추가, §4.12.1).
   - (a)와 (b)는 compose env와 호스트 마운트를 바꾸는 거버넌스·운영 변경이다. `test_deployment_env_passthrough.py`가 고정한 hermes 경계를 건드리지 않는지 함께 확인한다.
4. **실패 동작:** 일치한다. 모르는 읽기는 `VERB_NOT_PERMITTED`, 정책 미허가는 `CONTROL_VERB_NOT_GRANTED`로 거부되고 아무것도 읽지 않는다. 읽기는 기록을 남기지 않으므로 행위자 표기(`assistant_bridge`)는 판정에 영향이 없다.

### 4.8.1 W-14·W-15 소스 대조 (2026-10-13)

**방법:** origin/main `1b45ed57`의 소스만 읽었다. 세 명령과 read-bridge를 실행하지 않았고, 상태 파일이나 실제 출력도 보지 않았다. 아래의 "같다"는 코드 경로가 같다는 뜻이다. 같은 시각에 두 경로의 실제 값을 비교한 것은 아니다.

**read-bridge 요청·응답·행위자 (VERIFIED_FROM_SOURCE)**
- **요청:** `{"command": "<읽기 이름>"}`과 envelope(`proto`, `client_id`). 모르는 이름은 `VERB_NOT_PERMITTED`로 거부된다(`read_bridge.py:157-170`).
- **응답:** `{ok, command, reply(텍스트), action}`과 `data`다. `data`는 처리기가 구조화된 값을 돌려줄 때만 그 dict이고, 아니면 `plain(outcome)`이다(`read_bridge.py:224-235`). `crypto_readiness`는 `(text, readiness_data)`를 돌려준다(`domain_console.py:114-120`). `crypto_status`는 텍스트만 돌려준다(`domain_console.py:106-111`).
- **행위자:** `ASSISTANT_ACTOR`. 읽기는 아무것도 기록하지 않는다(`read_bridge.py:46-49`, `:194-196`).
- **허가:** `crypto_status`와 `crypto_readiness` 모두 정책의 `assistant_read.verbs`에 있다(`GOVERNANCE_POLICY.yaml:354-355`). 정책 게이트 대상은 아니다.

**W-14 `register_execution_stage --show` ↔ read-bridge**

| 항목 | `--show` (`register_execution_stage.py:77-116`) | `crypto_status` (`dashboard.py`) | `crypto_readiness` `data.execution_stage` (`live_readiness.py:1063`, `:1185-1186`; `readiness_model.py:548`) |
|---|---|---|---|
| 단계 판정 | `es.resolve_execution_stage(base, now, approval_store=ApprovalStore.default(base))` | **없다.** `dashboard.py`에 execution stage 참조가 없다 | `resolve_execution_stage(root, now)`. `approval_store`가 없으면 resolver가 같은 `ApprovalStore.default(root)`를 쓴다(`execution_stage.py:596-599`) |
| 필드 | 텍스트 10줄. `--json`이면 `status`(`as_dict`), `record`(등록 기록 전체), `policy`(실행 중 정책 정체성) | — | `as_dict` 9개(`stage`, `valid`, `reason_code`, `recorded_stage`, `stage_id`, `record_sha256`, `policy_version`, `approval_id`, `anchor_behind`; `execution_stage.py:254-260`)와 `enforced`, `admits_entry` |
| `--show`에만 있는 것 | 단계 원장 행 수·tip seq·검증 실패 reason, anchor seq·검증 실패 reason, 실행 중 정책 버전과 safety sha256, 기록의 `testnet_cycle_id`, 등록 기록 전체(`--json`) | — | 없음. 따로 있는 `testnet_evidence`(획득한 testnet cycle 목록)는 기록이 인용한 cycle id와 같은 값이 아니다 |
| 데이터 출처 | 상태 루트의 단계 기록·원장·anchor, 승인 저장소, 이미지 안 정책 파일 | — | 같은 상태 루트(read-bridge는 상태 루트 전체를 마운트, `docker-compose.yml:445-446`), 같은 이미지의 정책 파일 |
| 프로세스·시점 의존 | `now`. `execution_stage.py`와 `policy_fingerprint.py`에는 env 읽기가 없다 | — | 같음. `live_readiness`의 READY 판정과 달리 단계 판정은 프로세스 env를 쓰지 않는다 |
| 부수 효과 | 판정 경로(`execution_stage.py:553-660`)와 세 reader에 쓰기·잠금 호출이 없다. 승인 저장소 `get`은 잠금 없는 읽기다(`approval_store.py:87-88`) | — | 같은 resolver. 다만 `live_readiness` 보드 전체의 다른 검사가 부수 효과 없이 끝나는지는 이번 범위가 아니다(NOT_VERIFIED) |
| 비root 실행 | 호스트에서는 상태 루트에 닿지 못해 불가. `--show`는 root 가드를 부르지 않는다. 문서의 경로는 `docker exec` | — | read-bridge 경유이므로 호스트 계정 권한과 무관. 단 D-11 접근 전제 |

**W-14 판정: B(판정 목적) / D(진단).** "이 기계가 어떤 단계로 읽히고, READ_ONLY라면 왜인가, 새 진입을 허용하는가"는 `crypto_readiness`의 `data.execution_stage`가 같은 resolver로 답한다. 원장·anchor의 검증 세부, 실행 중 정책 해시, `testnet_cycle_id`는 read-bridge에 없다. 이 값들은 단계 원장을 고칠 때(`--sync-anchor`, 상태 쓰기) 보는 진단이므로 Thomas의 작업(D)으로 둔다.

**W-14 조건:**
- D-11이 풀려야 Claude 계정이 read-bridge에 닿는다.
- read-bridge와 scheduler가 같은 이미지를 돌려야 정책 정체성이 같다. 2026-10-12 관측에서는 여덟 서비스가 같은 이미지였다. 지금은 NOT_VERIFIED다.

**W-15 `promote_strategy_candidates --list` ↔ read-bridge**

| 항목 | `--list` (`promote_strategy_candidates.py:586-822`) | `crypto_status` (`dashboard.py:186-200`, `:255-268`) |
|---|---|---|
| 내용 | 후보별 한 줄. 열은 `candidate_id`, `strategy_id`, `generation_id`, family, `champion_score`, verdict, oos(holdout), `win_rate`, rr(기준), closed, provenance와 비용 기준·창 깊이·선택 보정 표시(`:814-821`). 머리에는 비용 모델, 기준·깊이 혼재, door가 거부할 행(파생 격리, 기록 미서명, 중복·근접 군집) 경고가 붙는다 | 승격 대기 건수와 임계(`--list`를 보라는 안내 포함), timeframe·방향별 퍼널 집계, fusion parent 적격 수와 읽은 후보 수 |
| 형식 | 텍스트만(`--json` 없음) | 텍스트만(read-bridge `data`는 `plain`) |
| 출처 | 후보 저장소(`pool_state.read_candidates`, 행 해시 검증 읽기, `pool_state.py:285-305`), 순위 함수(`candidate_ranking`), 비용 모델 상수 | 같은 후보 저장소와 활성 풀 |
| 부수 효과 | `--list` 분기는 root 가드(`:833`) 전에 끝난다. 후보 읽기는 스트림 읽기이고, `candidate_ranking.py`에는 쓰기 호출이 없다. 머리 경고에 쓰이는 나머지 `pool` 도우미(중복 군집, 파생 격리)의 부수 효과는 NOT_VERIFIED | — |
| 프로세스·시점 의존 | env 읽기 없음(스크립트, `candidate_ranking.py`, `pool_state.py`, `pool.py` grep) | — |

**W-15 판정: D(권고), C는 선택지.**
- `crypto_status`는 후보에 대해 집계만 준다. 후보별 행, 순위, door 거부 경고는 없다. 의미가 다르므로 B가 아니다.
- `--list`는 승격을 정하는 운영자 화면이다(`:592-594`). 승격 자체는 Thomas 승인 경로이고 상태를 쓴다(W-10과 같은 D). 그래서 목록도 Thomas가 보는 것을 기본으로 둔다.
- Claude가 승격 보고를 준비해야 한다면, read-bridge에 후보 목록 읽기를 더해야 한다(C). 이는 door 동작 목록, 정책 `assistant_read.verbs`, 정책 고정 테스트를 바꾸는 거버넌스 변경이다(D-15).

### 4.8.2 W-11·W-12 dispatch door 대조 (2026-10-13)

**방법:** origin/main `1b45ed57`의 소스만 읽었다. CLI와 dispatch door를 실행하지 않았고, 승인 요청이나 상태 변경 요청도 만들지 않았다. 원장·상태 파일·`.env`도 보지 않았다. 운영 중인 door의 실제 설정은 compose 파일로만 판단했다(NOT_VERIFIED: 지금 떠 있는 컨테이너의 명령줄).

**두 경로**
- **CLI intake** (`runtime/mvp_runtime/cli.py:96-262`): 동기 실행이다. 입력은 플래그 6종과 요청 텍스트(인자 또는 stdin)다. 모르는 `-` 토큰은 `EXIT_USAGE`로 거부한다(`:153-160`). 이어서 kill-switch(`:177-184`), Safety-Flag Gate의 provider·검증 provider·검색 도구·writer 선택(`:188-200`)을 거쳐 `run_task(channel="manual", priority=…, tiered_provider_selector=…, write_path=…)`를 부른다(`:219-230`). 결과 텍스트는 stdout, `BLOCKED <reason_code>`는 stderr로 나가고, 종료 코드는 0, 2, 3 중 하나다(`:231-258`).
- **dispatch door v2** (`runtime/mvp_runtime/dispatch_bridge.py:212-414`): 닫힌 키 `request`, `kind`, `reason`, `naver_keywords`, `request_id`와 envelope만 받는다(`:107-109`, `:247-252`). 검증하고 pipeline-worker로 넘길 뿐 직접 실행하지 않는다. 거부 코드는 다음과 같다.
  - 키·형식: `ARGUMENT_NOT_ACCEPTED`, `MALFORMED_REQUEST`, `REQUEST_REQUIRED`
  - kind·사유: `KIND_NOT_PERMITTED`(kind 4종, `:97`), `REASON_REQUIRED`
  - seed 길이: `MALFORMED_REQUEST`(`MAX_SEED_CHARS`, `:280-293`)
  - 런타임 상태: kill-switch(`:323-329`), `V2_INTAKE_CLOSED`(`:296-312`)
  - 연결: `WORKER_UNAVAILABLE`(`:333-338`, `:366-372`)
- **worker 재검사** (`runtime/mvp_runtime/pipeline_worker.py:206-412`): 키(`:237-242`), kind(`:256-267`), 사유, 행위자 프로필(`:275-291`), kill-switch를 다시 본다. 프로필의 고정 요청자로 `run_task`를 부른다(`:352-372`).
- **v2 intake:** 커밋된 compose는 `--v2-intake closed`를 주지 않고 `MVP_DISPATCH_V2_INTAKE`도 넘기지 않는다(`docker-compose.yml:532-547`). 그래서 기본값 `open`이다(`dispatch_bridge_cli.py:80-81`). `RUNBOOK_WORKFLOW_CUTOVER.md:33-40`의 cutover가 실행되면 새 단일 요청은 거부되고 v3 `workflow.submit`만 남는다.

**비교**

| 질문 | CLI | dispatch door | 판단 |
|---|---|---|---|
| 1. 필수 입력 표현 | 텍스트, `--kind`(planner의 `REQUEST_KIND_CAPABILITIES` 5종, development 포함, `planner.py:81-90`. CLI가 어디서 거부하는지는 NOT_VERIFIED), `--naver-keywords` | 텍스트, kind 4종(development 없음), `naver_keywords`. **`reason` 필수** | 분석·조사·번역·콘텐츠 요청은 표현된다. development와 아래 플래그는 표현되지 않는다 |
| 2. 의미 | `--independent-validation`(always/auto)·`--revise`는 요청마다 켜고 끈다. `--important`는 priority HIGH. `--write-output`은 Safety-Flag Gate 아래 workspace 쓰기. `tiered_provider_selector`로 난이도별 모델 tier | 검증 정책이 서비스 명령에 고정된다: `--independent-validation auto`, `--revise`(`docker-compose.yml:619-632`). priority 없음(기본 NORMAL, `intake.py:148`). `write_path` 거부. worker에는 tier 선택기가 없다(`pipeline_worker.py` grep) | **다르다.** 같은 텍스트라도 검증·재생성·모델 tier가 달라질 수 있다 |
| 3. 요청자·감사 | `requester_id`를 넘기지 않으므로 `build_task` 기본값 `thomas` / `real_thomas` / `authenticated=True`, 채널 `manual`(`intake.py:135-139`, `cli.py:219-222`). **지금 Claude가 CLI로 돌린 실행은 원장에 Thomas 본인의 요청으로 남는다** | `assistant_bridge` / `agent` / 채널 `agent`. `source_ref`는 `assistant_bridge:dispatch: <reason>`(`pipeline_worker.py:157-173`). `client_id`는 `created_by`에 들어가는 자유 텍스트이고, 권한에 쓰이지 않으며 인증되지도 않는다(`:344-347`). door의 요청 기록은 peer uid를 남기지 않는다(`bridge_idempotency.py`에 uid 기록 없음) | **다르다.** dispatch가 더 정직하다(Thomas가 아님). 그러나 Claude와 Hermes를 구별하지 못한다. 지출 감시와 task id seed도 같은 `requester_id`를 쓴다(`dispatch_spend.py:17-20`, `pipeline_worker.py:345-347`) |
| 4. 승인·권한 | 분석 실행에 승인 검사가 없다. kill-switch와 Safety-Flag Gate가 있다 | v2 분석도 승인 검사가 없다(효과 등급으로 제한, `dispatch_bridge.py:1-8`). kill-switch는 door와 worker 두 곳에서 본다. provider gate는 worker 환경에서 같은 방식으로 고른다 | 분석 실행만 보면 같은 수준이다. `requester_type`은 permission·planner·pipeline이 읽지 않는다(grep). 그래서 Thomas로 기록되는 것이 권한을 넓히지는 않는다(NOT_VERIFIED: 그 밖의 모든 소비자) |
| 5. 중복·재시도 | idempotency가 없다. 다시 실행하면 새 실행이다 | `request_id`(선택)로 claim → complete → replay한다. 같은 id에 다른 내용이면 거부한다. 거부되면 id를 해제한다(`dispatch_bridge.py:314-319`, `:343-405`). `request_id`가 없으면 CLI와 같다. 효과 도중 충돌하면 TTL 뒤 재실행될 수 있다는 한계가 문서화돼 있다 | dispatch가 더 강하다. 의미는 다르다 |
| 6. 오류 보존 | stderr `BLOCKED <code>: <message>`, 종료 코드, 원장 기록 여부(`LEDGER: NOT recorded`) | 거부는 `{ok:false, reason_code, reason}`, worker 거부는 그 reason_code로 전달된다. 실행 BLOCK은 `kind`를 담은 응답이다. 전송 세부는 door 로그에만 남는다(`WORKER_UNAVAILABLE`) | reason code는 보존된다. 원장 기록 실패를 보고하는지는 NOT_VERIFIED |
| 7. 권한 범위 | 분석 실행. `--write-output`이면 gate 아래 workspace 쓰기 | v2 분석은 CLI보다 좁다(쓰기 없음, kind 4종). **그러나 같은 소켓의 v3 명령**(`:120-131`)이 workflow 제출·취소·재시도·수정 제안, 그리고 `schedule.propose_change`를 받는다. 정책의 `assistant_schedule`은 `mutation_allowed: true`다(`GOVERNANCE_POLICY.yaml:398-412`): analysis_task·workflow_plan 스케줄을 범위 안에서 만든다. door 접근은 uid 단위이고 동작 단위가 아니다 | **넓다.** Claude uid를 이 door에 넣으면 CLI에 없던 스케줄 생성 권한이 함께 열린다 |

**보안 경계 관찰 (D)**

| 항목 | 결과 | 근거 |
|---|---|---|
| 임의 CLI 플래그 | 전달되지 않는다. 모르는 키는 door와 worker 둘 다 거부한다 | `dispatch_bridge.py:247-252`, `pipeline_worker.py:237-242` |
| 임의 경로 | 경로 키가 없다. `write_path`·`writer`는 거부된다 | `dispatch_bridge.py:29-31`, `:103-109` |
| 임의 명령 | door·worker·CLI 어디에도 `subprocess`나 셸 호출이 없다 | grep |
| 행위자 위조·누락 | 요청자는 프로필 상수다. `actor_profile`은 door의 키 목록에 없어 바깥에서 지정할 수 없다(`pipeline_worker.py:145-147`). worker 내부 소켓은 uid 10001만 허용한다. 그러나 `client_id`는 caller가 정하는 자유 텍스트다. Claude와 Hermes는 같은 `assistant_bridge`가 된다 | 위 줄 번호 |
| 승인 검사 생략 | v2 분석에는 원래 승인이 없다(설계). v3 스케줄 변경은 정책 범위 안에서 승인 없이 적용되고, 범위 밖은 제안으로만 기록된다 | `GOVERNANCE_POLICY.yaml:398-412` |
| 같은 요청의 중복 처리 | `request_id`가 없으면 막지 않는다 | `bridge_idempotency` |
| 허용되지 않은 종류 | door와 worker 둘 다 `KIND_NOT_PERMITTED` | `dispatch_bridge.py:269-273`, `pipeline_worker.py:262-267` |

코드를 바꾸지 않았다. 후속 조치는 결정 항목에만 적는다(D-14).

**W-11 판정: E 유지.**
- 대체는 소스로 입증되지 않았다. 요청자 기록, 검증 정책, 기능 범위, door 접근 범위 네 가지가 다르다.
- 선택지:
  - (a) **D**: Claude는 intake를 직접 돌리지 않는다. Thomas가 CLI를 쓰거나 Hermes가 dispatch한다.
  - (b) **C**: Claude 전용 행위자 프로필과 Claude uid용 v2 전용 경로를 만든다. v3·스케줄은 빼고, 필요한 assurance 옵션의 범위도 정한다. 새 인터페이스이고 정책·테스트 변경이다.
  - (c) dispatch door를 그대로 쓴다. Claude를 Hermes와 같은 행위자로 기록하고, 스케줄 위임 권한도 함께 받아들인다.
- 권고 기본값은 (a)다. (c)는 권한이 넓어지므로 권하지 않는다.

**W-12 판정: E 유지.**
- W-11과 같은 이유다.
- Naver 근거 수집 자체는 같은 pipeline-worker env에서 돈다. dispatch가 `naver_keywords`를 그대로 넘기므로 근거 수집 능력은 같다.
- 차이는 두 가지다. door가 seed 길이를 먼저 거부한다. CLI 실행은 Thomas로 기록된다.

**별도로 드러난 사실:**
- 지금 Claude가 `docker exec`로 CLI intake를 돌리면 원장에 `real_thomas`로 남는다.
- 분리 후에는 Claude가 CLI를 돌리지 못하므로 이 기록은 사라진다.
- 분리 전에도 Claude 실행이 Thomas로 기록된다는 점은 감사 해석에 영향을 준다. 이 문서는 운영 기록을 바꾸지 않는다.

### 4.8.3 W-13 READY 판정 경로 대조 (2026-10-13)

**방법:**
- origin/main `1b45ed57`의 소스와 테스트만 읽었다.
- `live_readiness` CLI, read-bridge, 운영 채널은 실행하지 않았다. 실제 출력값과 운영 시점의 READY 여부도 확인하지 않았다.
- 합성 데이터(`tmp_path`, 실행 env를 지우는 `clean_env`)만 쓰는 readiness 테스트 두 파일을 돌렸다. import 경로는 worktree임을 확인했다.

**존재 확인:**

| 이름 | 실제 구현 | READY 의미 |
|---|---|---|
| `crypto_status` | `domain_console.py:106-111` → `dashboard.build_status` | **READY가 없다.** dashboard에 readiness·live gate 표시가 없다(grep) |
| `crypto_readiness` (read-bridge, 운영 채널 `/crypto readiness`) | `domain_console.py:114-120` → `live_readiness.build_readiness` + `readiness_data` | 아래 세 값 |
| `live_readiness` CLI | `live_readiness.py:1566-1587` → 같은 `build_readiness` | 아래 세 값. 종료 코드는 `ready` |
| scheduler·주문 경로 | readiness 모듈을 import하는 곳은 `domain_console.py:115`와 CLI뿐이다(grep). 주문 경로는 실행 단계와 guard를 스스로 다시 판정한다(`live_execution.py:300-308`, `live_order.py:539`, `promotion.py:254`) | readiness 결과를 쓰지 않는다 |
| `execution_stage` | `execution_stage.resolve_execution_stage`(§4.8.1) | 단계 판정 |

holdings의 `ready`(`holdings/cash_flows.py`, `holdings/store.py`)는 다른 개념이라 대상에서 뺐다.

**같은 보드가 내는 세 가지 답** (`live_readiness.py:551-1206`)

| 값 | 정의 | 근거 | 의미 |
|---|---|---|---|
| `ready` | 22개 검사가 모두 통과(`:1170`) | 검사 중 5개(`ENV_SCOPED_CHECKS`: opt-in, 확인 문구, 거래소 읽기 가시성, 거래 계좌 자격, 시세 가시성, `:1215-1221`)는 **보드를 돌리는 프로세스의 env**에서 계산한다(`:567`, `:774-785`, `:1026-1049`). 나머지는 상태 파일·원장·정책에서 계산한다 | "**이 프로세스**에서 실제 주문이 나갈 수 있는가". CLI 종료 코드가 이것이고 스크립트 전제조건으로 쓰도록 문서화돼 있다(`:44-47`). 기록된 gate는 이 값을 바꾸지 못한다(`:1166-1169`; 테스트 `test_the_recorded_gate_cannot_move_the_ready_verdict`) |
| `live_entry_possible` | 구성요소 11개의 3값 AND. False가 하나라도 있으면 False, 아니면 None이 하나라도 있으면 None, 모두 True일 때만 True(`readiness_model.py:463-495`) | trading env가 있는 프로세스(`live_trading_opt_in` 통과)는 자기 스위치와 계좌 읽기로 답한다. 그 밖의 프로세스는 **trading 프로세스가 마지막 fire에 남긴 기록**으로 답한다(`readiness_model.py:467-469`, `:47-53`). 기록이 오래되면 None이다(gate 기록 2시간, `live_readiness.py:164`; cycle은 주기 3회분) | "자율 경로가 **다음 cycle에** 포지션을 열 수 있는가". 주문 단위의 용량(노출 한도, 동시 포지션, 쿨다운)은 포함하지 않는다(`readiness_model.py:61-63`) |
| `execution_stage` | `resolve_execution_stage`(`live_readiness.py:1063`) + `enforced`, `admits_entry` | 공유 resolver. env를 읽지 않는다(§4.8.1) | 기계가 어느 단계로 읽히는가 |

**경로별 비교**

| 질문 | scheduler에서 CLI(지금 W-13) | read-bridge `crypto_readiness` |
|---|---|---|
| env 기반 5개 검사 | scheduler의 실제 env로 판정 | read-bridge env는 peer 변수 두 개뿐이다(`docker-compose.yml:469,488`). 그래서 5개 모두 실패하고, 화면에는 "이 컨테이너에서는 볼 수 없음"으로 표시된다(`:1225-1240`) |
| `ready` | scheduler가 주문을 낼 수 있는지 | **구조상 항상 False.** read-bridge 컨테이너는 주문을 낼 수 없다. scheduler의 `ready`와 **비동등**이다 |
| `live_entry_possible` | 자기 env와 자기 계좌 읽기 | 기록 기반. 최대 한 주기 늦고, 오래되면 None이다. 같은 모델과 같은 구성요소이고, 신선한 기록이면 True를 낼 수 있다(테스트 `test_a_ready_machine_reads_true_from_a_console`, `test_a_console_never_reads_its_own_env_rows_as_the_gate`). **부분 동등** |
| 일일 손실 breaker | 계좌 피드가 설정돼 있으면 거래소 계좌를 한 번 읽는다(소켓 1개, 읽기 전용, `:22-28`, `:786-800`) | 계좌 피드 env가 없어 소켓을 열지 않는다. 행은 "이 컨테이너에서 볼 수 없음"이고, `risk_ready`는 기록에서 읽는다(`:1238-1240`) |
| `execution_stage` | 공유 resolver | 같음. **동등** |
| 결과 전달 | 텍스트 또는 `--json`(status 전체 + readiness), 종료 코드 0/1 | `{reply(텍스트), data(readiness_data)}`. `data`에는 `infrastructure_ready`, `env_scope: "this_process"`, 검사별 ok, armed 수, 기록된 gate, `live_entry_possible`, 구성요소, 실행 단계, 계약 상태가 들어간다(`readiness_model.py:496-557`). 검사별 상세 문구는 `reply` 텍스트에만 있다 |

**공통 검사와 다른 검사:**
- **공통:** 같은 `build_readiness`가 같은 22개 검사를 같은 상태 루트에서 계산한다. 예산, 위험 한도, 런타임 제어, armed 전략, breaker, entry marks, 주문 전 snapshot, 실행 단계, 거래소 계약 등이다.
- **다른 것:** env 기반 5개 검사, 계좌 소켓 읽기, 그리고 `live_entry_possible`의 gate·risk·account·시세 구성요소가 어디서 근거를 가져오는가다(자기 env 또는 기록).

**데이터 의존성과 부수 효과:**
- 상태 루트의 cycle 원장, 예산·위험 한도 기록, 제어 상태, 실행 단계 기록, 거래소 계약 기록, 주문 전 snapshot, entry marks를 읽는다.
- 보드는 자신이 "writes nothing"이라고 적는다(`live_readiness.py:6`).
- 쓰기 호출은 `live_readiness.py`와 guard dry-run(`live_order.py`의 `evaluate_live_order_guard`)에서 grep으로 찾지 못했다.
- 네트워크는 계좌 피드 env가 있는 프로세스에서만 쓴다.
- 나머지 하위 reader(`count_today`, `compute_open_notional_usdt`, 계좌 모듈)는 NOT_VERIFIED다.

**READY는 실행 권한이나 승인이 아니다:**
- 보드는 "holds no authority of its own"이다(`live_readiness.py:6`).
- 주문 경로는 readiness를 읽지 않고 실행 단계와 guard를 스스로 판정한다.
- `ready`가 True라는 것은 그 프로세스에서 주문이 *나갈 수 있다*는 능력의 진술이다.
- `live_entry_possible`이 True라는 것은 다음 cycle에 자율 진입이 *가능하다*는 진술이다.
- 어느 것도 Thomas의 승인이나 주문 허가가 아니다.
- 단계 전환 자체는 승인 경로(`register_execution_stage --request/--confirm`)가 따로 정한다.

**W-13 판정: E 유지.**
- 동일한 resolver를 쓰는 것은 `execution_stage` 한 값뿐이다. 보드 전체는 실행 프로세스에 따라 다른 질문에 답한다.
- Claude가 W-13으로 알고 싶은 것을 두 가지로 나눌 수 있다.
  - "지금 자율 진입이 가능한가, 무엇이 막는가": read-bridge의 `live_entry_possible`, `blocking`, `unknown`이 같은 모델로 답한다. 다만 기록 기반이라 한 주기 늦고 오래되면 None이다(부분 동등).
  - "scheduler 프로세스가 주문을 낼 수 있는가"(`ready`, 종료 코드): read-bridge로는 얻을 수 없다(비동등). 이것은 trading 프로세스 쪽 스크립트 전제조건이다. Claude는 돈 경로를 만지지 않으므로 D로 두는 것이 자연스럽다.
- 기록 기반 답이 Claude의 용도에 충분한지는 운영 판단이다(D-14).

**확인하지 못한 것 (NOT_VERIFIED):**
- 구성요소 11개가 각각 주문 door의 거부 조건과 정확히 같은지(`readiness_model`의 각 `_*_component`를 끝까지 대조하지 않았다)
- 하위 reader와 계좌 모듈의 부수 효과
- 실제 운영 컨테이너의 env

**별도 감사 이슈 (W-13과 무관, §4.8.2에서 유지):**
- CLI intake 실행이 `real_thomas`로 기록되는 것
- dispatch door와 CLI의 행위자 기록·접근 범위 차이

### 4.8.4 W-20 `docker logs` 사용처 대조 (2026-10-13)

**방법:**
- origin/main `1b45ed57`의 문서와 소스만 읽었다. `docker logs`를 포함해 어떤 Docker 명령도 실행하지 않았다.
- 컨테이너 로그·env·상태, read-bridge의 실제 응답도 보지 않았다.
- 사용처는 저장소 전체에서 `docker logs`·`docker compose logs`를 grep해 찾았다(`BUILD_HISTORY.md`와 `docs/history/`의 과거 기록은 제외).

**사용처와 판정**

| # | 사용처 | 목적 | 필요한 최소 정보 | 기존 대체 경로 | 판정 |
|---|---|---|---|---|---|
| L1 | `DEPLOYMENT.md:186`(배너 출처 `operator_cli.py:152`) | frontdesk 대화 모드가 실제로 켜졌는지 배포 뒤 확인 | 켜짐/꺼짐 한 값 | 없음. `runtime_status`·`heartbeat`에 frontdesk 상태가 없다(grep) | **C**: 실행기 사후 확인의 고정 패턴(D-16) |
| L2 | `DEPLOYMENT.md:208` `docker compose logs -f scheduler` | 배포 뒤 scheduler가 정상으로 도는지 관찰 | lane별 생존, 최근 fire 결과 | read-bridge `heartbeat`(lane 3개의 FRESH/STALE, `store_reads.py:55-58,130-140`), `scheduler_events`(최근 최대 100개, `:43,101-127`), `runtime_status` | **B**(목적 기준). 실시간 원문 추적은 **D** |
| L3 | `DEPLOYMENT.md:556`, `RUNBOOK_APPROVAL_PATH_DRILL.md:86` | 각 door의 uid 허용 목록이 켜져 있는지 확인(경계 설정 점검) | door별 `uids=[…]` 토큰 | 없음. 이 배너는 각 door CLI가 기동할 때 서비스 로그로만 출력한다(`socket_door.py:701-714`; 호출 `read_bridge_cli.py:61`, `dispatch_bridge_cli.py:151`, `switch_bridge_cli.py:59`, `knowledge_bridge_cli.py:60`, `pipeline_worker_cli.py:132`). dispatch v3 `capabilities`는 `v2_intake`·workflow manager 상태만 준다(`dispatch_bridge.py:452-460`) | **C**: 실행기 사후 확인의 고정 패턴(D-16) |
| L4 | `DEPLOYMENT.md:618` | worker의 검증 플래그(`validation=auto, revise=on`)가 실제 서비스에 적용됐는지 확인 | 두 값 | 없음. 기동 배너뿐이다(`pipeline_worker_cli.py:130`) | **C**: 같음 |
| L5 | `RUNBOOK_HARNESS_BACKUP_RESTORE.md:311` | 위험 lane watchdog가 fire를 끊은 뒤의 stack 확인(사고 진단) | 사고 여부, 어느 lane, 언제 | 사고 여부는 `heartbeat`의 STALE(overrun)로 알 수 있다(`heartbeat.py:17-21`). watchdog JSON 기록을 read-bridge가 노출하는지는 NOT_VERIFIED. stack 원문은 대체 경로가 없다 | 사고 감지는 **B**, stack 원문은 **D**(Thomas) |
| L6 | `socket_door.py:294-304` | door의 예상 못 한 예외 traceback. 응답에는 `error_ref`만 주고, 원문은 서비스 로그로만 보낸다 | 운영자가 `error_ref`로 대조 | 설계상 assistant 쪽에 주지 않는다: "file paths under the state directory, store layout, a dependency's own error text … helps someone who has talked the assistant into probing" | **D**: Claude는 덜 신뢰하는 쪽이다. `error_ref`는 응답으로 이미 받는다 |
| L7 | `health_watch.sh:365` | 운영자 알림 메시지의 안내 문구 | — | Thomas가 받는 알림이다 | **D**(Thomas) |
| L8 | `scheduler.py:2640-2655`, `tests/test_mvp_runtime_scheduler.py:1036-1043` | 과거(2026-08-06)에 실행 예산이 얼마나 자주 걸리는지 로그로만 볼 수 있었다는 기록 | deferral 횟수 | 이제 `deferred` 이벤트로 원장에 남는다(`scheduler.py:81`, `:2685-2686`, `:2703`). `scheduler_events`로 최근 100개까지 보인다. 여러 날의 집계는 그 범위를 넘으면 없다 | **B**(최근 범위). 긴 기간 집계는 이번 범위 밖 |

`RUNBOOK_HARNESS_BACKUP_RESTORE.md:398`은 사용처가 아니다(로그가 백업 대상이 아니라는 기록). 그래서 표에서 뺐다.

**원문 로그가 정말 필요한가:**
- 필요하지 않다. 위 목적은 다음으로 채워진다.
  - 한 값 또는 짧은 토큰: L1, L3, L4
  - 이미 정규화된 상태: L2, L5 감지, L8
  - Claude가 볼 이유가 없는 사고 원문: L5 stack, L6, L7
- 원문을 Claude에게 주는 방식은 기본안에서 뺐다. 이유는 두 가지다.
  - 서비스 로그에는 여러 모듈이 stderr로 쓴다. 예외 문구와 traceback에 무엇이 들어가는지 소스로 증명할 수 없다(NOT_VERIFIED).
  - door 설계가 이미 traceback을 assistant 쪽에서 떼어 놓았다(L6).

**C 인터페이스 요구 조건 (설계 초안, 구현 아님, D-16):**
- 실행기 코드 안의 닫힌 목록: (서비스, 패턴, 돌려줄 필드). 예: read·switch·dispatch·knowledge door의 `uids=`, worker의 `validation=`·`revise=`, operator의 frontdesk 켜짐 여부.
- Claude는 서비스나 패턴을 지정하지 못한다. 동작 인자는 PR 번호뿐이거나 인자가 없다.
- 돌려주는 것은 일치한 토큰 또는 "일치 없음"뿐이다. 원문 줄, 주변 줄, 일치 개수 밖의 정보는 돌려주지 않는다.
- 읽는 범위는 컨테이너당 마지막 N줄로 고정한다. 최근 기동 배너만 필요하기 때문이다.
- 실행기 감사 기록(§4.5)에 남긴다. 지금의 `docker logs`는 아무 기록도 남기지 않는다.
- Docker 소켓 접근은 실행기 안에만 있다. Claude 쪽 전제가 아니다.
- **대안:** 각 서비스가 기동 설정을 상태 파일에 쓰고 read-bridge가 읽게 하는 방식. 이것은 런타임 코드·정책 `assistant_read.verbs`·테스트를 바꾸는 거버넌스 변경이다.

**감사·민감 정보 우려:**
- 지금 `docker logs`는 root인 Claude가 아무 기록 없이 읽는다.
- read-bridge 읽기도 아무것도 기록하지 않는다(`read_bridge.py:46-49`).
- 실행기 경유는 요청자 uid와 동작이 감사에 남는다.
- read-bridge 대체(B)는 감사 추적을 더하지 않는다. 지금과 같은 수준이다.

**W-20 판정:**
- 사용처별 B·C·D로 판정했으므로 E에서 뺐다.
- C의 범위와 방식은 D-16에서 정한다. B는 D-11(read-bridge 접근)이 풀려야 쓸 수 있다.

**확인하지 못한 것 (NOT_VERIFIED):**
- 서비스 로그에 Secret이나 민감 값이 들어가지 않는지
- watchdog JSON 기록이 read-bridge로 보이는지
- 실제 운영 서비스의 기동 배너 내용
- 로그 보존 설정의 실제 값. compose 주석은 10m × 3이라고 적는다(`scheduler.py:2647-2648`).

### 4.9 C1: 자동화·cron 이전 영향

**cron 3줄은 NOT_VERIFIED다.** 2026-10-12에 root crontab의 비주석 줄 수(15)와 "claude" 포함 줄 수(3)만 셌다. 이번에는 다시 읽지 않았다. 저장소가 정의한 root cron 5개(§4.7)는 경로에 "claude"가 들어가지 않는다. 그 5개가 15줄에 들어 있는지도 확인하지 않았다.

| 자동화 | 목적 | 실행 계정 | 파일 접근 | Docker | Secret | 비root 이전 영향 | 이전 전 검증 | 승인 |
|---|---|---|---|---|---|---|---|---|
| `blog-daily-draft.sh` | 매일 블로그 초안(headless Claude), scripts·skills 자동 커밋 | root(이전 기록, NOT_VERIFIED) | `~/.claude`(자격·skills·scripts), 볼트 `/root/obsidian-thomas` | 없음(파일 grep 기준) | Claude 구독 자격 | 볼트와 `~/.claude`를 새 계정이 소유해야 한다. 볼트는 pipeline-worker도 읽기 전용으로 마운트하므로(`docker-compose.yml:604-612`, 실제 경로는 `.env` 값이라 미확인), 옮기면 compose 값도 바뀐다 | crontab 줄, 볼트 경로 = `THOMAS_BLOG_VAULT_DIR` 여부 | 필요(cron·볼트 이전) |
| 사용자 hook(`vault-autopush` 외) | 볼트 자동 push, 볼트 쓰기 감시, 대기 감시 | 세션 계정(root) | 볼트, `~/.claude/scripts` | 없음 | 볼트 push 자격(위치 미확인) | 새 홈으로 옮기면 그대로 동작(DESIGN_ASSUMPTION) | push 자격이 어느 파일에 있는지 | 필요(설정 이전) |
| `session-start-check.sh` | 세션 시작 시 MEMORY 크기 경고, 호스트 요약 | 세션 계정 | 저장소, `~/.claude` | **있음**(`:38-41`) | 없음 | docker 요약이 비게 된다(실패 허용) | 없음 | 설정 이전과 함께 |
| `hermes-notify.sh` | Mac 자동 수집 결과·실패를 Hermes 봇으로 발신 | Mac에서 `ssh thomas`(`:3`). SSH 계정은 NOT_VERIFIED | **`.env`**, 상태 루트의 `operator_registration.json` | 없음 | **있음**(봇 토큰) | 비root로는 실패한다. Claude 계정으로 옮기면 안 된다 | Mac의 SSH 대상 계정, 이 스크립트를 부르는 Mac 작업 목록 | 필요(위치·소유자 변경) |
| 그 밖의 이전 기록상 자동 작업: 서버 스크립트 볼트 복사(`server_scripts_backup.py`), 대화 수집, skills 자동 커밋 | 볼트로 사본 보관 | NOT_VERIFIED | 볼트, `~/.claude`, crontab 사본 | 미확인 | 미확인 | 미확인 | 각 작업의 실행 위치(cron인지 hook인지) | 미확인 |

**확정에 필요한 비민감 증거 (Thomas가 제공하거나, 승인하면 Claude가 확인):**
- root crontab 3줄의 실행 시각과 명령 경로. 환경 변수 할당과 그 값은 빼고 받는다.
- Mac 자동화가 SSH로 접속하는 계정 이름과 호출하는 서버 스크립트 경로.
- 볼트 push 자격이 있는 파일 경로. 내용은 필요 없다.
- 볼트 경로가 `THOMAS_BLOG_VAULT_DIR` 값과 같은지(예/아니오).

**지금 있는 위험 (분리 전이라도 알아야 할 것):** `hermes-notify.sh`는 Claude가 쓸 수 있는 디렉터리에 있다(`/root/.claude/scripts`, root 소유이지만 Claude가 root다). 이 스크립트가 `.env`에서 봇 토큰을 읽는다. Claude가 이 스크립트를 고치면 Secret이 출력될 수 있다. 지금은 행동 통제뿐이다. 이것도 Option A가 막으려는 경로와 같은 종류다. 분리할 때 이 스크립트는 Claude 계정 밖으로 옮겨야 한다(D-12).

### 4.10 C1: 승인 보안 가정과 잔여 위험

두 질문을 따로 본다. 이 둘을 섞으면 보안 수준을 과장하게 된다.
1. **서명이나 승인 기록 자체를 위조할 수 있는가.**
2. **승인 검증을 건너뛰도록 실행기·검증 코드·검증 설정을 바꾸거나, 실행기를 거치지 않고 Docker를 직접 부를 수 있는가.**

| 질문 | R1 Telegram | R2 SSH 서명 | R3 root 승인 파일 |
|---|---|---|---|
| 1. Claude 계정(분리 후)이 위조 | 못 한다. 전제: 상태 루트에 쓸 수 없고 봇 토큰도 읽을 수 없다(W-26 이전 포함) | 못 한다. 개인키는 Mac에 있다 | 못 한다. 파일은 root 소유 |
| 1. root·uid 10001 프로세스가 위조 | **가능**(`approvals.jsonl` 추가, 등록 파일 변경) | **서명은 못 한다** | 가능 |
| 2. Claude 계정이 우회 | 실행기 코드·설정·Docker에 쓸 수 없으면 못 한다. 이것은 서명 방식이 아니라 **파일 권한**이 보장한다 | 같음 | 같음 |
| 2. root가 우회 | **가능.** 서명을 위조할 필요가 없다. 실행기 코드나 허용 서명자 파일을 바꾸거나, Docker를 직접 부르면 된다 | **가능**(같은 이유) | 가능 |

**결론:**
- R2는 "승인 문서가 Thomas에게서 왔다"는 것을 root에 대해서도 증명한다.
- 그러나 "승인 없이는 배포되지 않는다"는 보장은 어느 선택지에서도 **root가 아닌 행위자에게만** 성립한다.
- root가 침해되면 승인 채널은 아무것도 지키지 못한다.
- 그래서 D-2의 근거에서 "호스트 root도 위조할 수 없다"를 "승인 출처의 증명이 강하다"로 고쳤다(§4.4, §8).

**실행기를 root로 돌릴 때(D-10 E1) 필요한 보장:**

| 항목 | 설계 | 보장 수준 |
|---|---|---|
| 실행기 코드 무결성 | Thomas가 검토한 origin/main SHA의 파일만 `/opt/thomas-deploy/<sha>/`에 root 소유 0755/0644로 설치. 설치 후 파일 해시 목록을 만들고, Thomas가 Mac에서 같은 SHA의 git 트리와 대조 | 비root에 대해서만. 실행기의 자기 검사는 자기 코드가 바뀌면 의미가 없으므로 넣지 않는다 |
| Claude가 코드·설정을 못 바꿈 | 설치본, systemd unit, 허용 서명자 파일, 철회 목록, nonce 기록, 감사 기록이 모두 root 소유이고 상위 디렉터리도 Claude 쓰기 불가. `python -I`, 고정 작업 디렉터리, 깨끗한 env로 실행해 Claude 쓰기 가능 경로에서 import하지 않음 | 파일 권한. 부정 시험 N-4, N-13, N-14 |
| 검증 실패 시 차단 | 승인 검증은 Docker 호출보다 먼저 실행된다. 검증 결과가 PASS가 아닌 모든 경우(파일 없음, 형식 오류, 서명 불일치, 만료, 철회, 소비됨, 결속 불일치, 예외)에 Docker 호출 0회 | 코드와 테스트. N-8–N-12. 아직 NOT_TESTED |
| 결속 | 승인 문서: 동작, PR, SHA, candidate id, rollback id, 프로젝트(`thomas_agent`), env 파일 경로, 만료, nonce. 실행기는 SHA를 자기 clone의 HEAD에서, 이미지 id를 실행 시점의 Docker에서 직접 읽어 비교한다. 요청에 들어온 값은 믿지 않는다 | 코드 |
| 재사용·만료 | nonce는 root 소유 기록에 소비 표시 후 실행. 만료는 호스트 시계 기준(시계 조작은 root 필요) | 코드 + 파일 권한 |
| root 침해 시 남는 위협 | `.env` 읽기, 임의 이미지 배포, 실행기·검증 설정 변경, 감사 기록 재작성(해시 체인 재계산), 승인 기록 위조(R1·R3) | 막을 수 없다. 탐지는 호스트 밖 증거뿐이다(Thomas가 Mac에서 대조하는 설치 해시, 승인 문서 사본) |

**같은 원리로 드러난 다른 가정:**
- **Claude가 main을 바꿀 수 있다.** Claude 계정의 GitHub 자격으로 PR을 병합할 수 있다면(#1221·#1222는 Thomas 승인 뒤 Claude가 병합했다), 실행기가 받는 origin/main은 Claude가 간접적으로 바꿀 수 있는 입력이다. 승인이 SHA에 결속되어 있으므로 Thomas가 승인하지 않은 SHA는 배포되지 않는다. 그러나 **실행기 코드의 설치본도 origin/main에서 오므로**, 설치는 Thomas가 SHA를 검토한 뒤에만 한다. 병합 권한의 범위는 D-7에서 함께 정한다.
- **Claude 설정 파일은 Claude 소유가 된다.** 분리 후 `~/.claude/settings.json`의 Guard·hook은 Claude가 고칠 수 있다. 이것은 경계가 아니다(Guard는 원래 tripwire). OS 권한이 경계를 맡는다. Guard를 계속 tripwire로 쓰려면 Claude가 쓸 수 없는 관리 설정 위치(root 소유)에 둔다. Claude Code의 관리 설정 파일 위치와 우선순위는 이번에 확인하지 않았다(DESIGN_ASSUMPTION, D-13).

### 4.11 C1 완료 기준과 상태

| # | 기준 | 상태 |
|---|---|---|
| 1 | root·Docker 의존 작업 인벤토리 | **완료**: W-01–W-30(§4.7) |
| 2 | 작업마다 권장 대체 경로 | **완료**: A·B·C·D·E 지정. E 3건은 근거·판단이 남음(W-14·W-15는 2026-10-13 판정, W-11·W-12는 소스 대조 후 E 유지와 선택지 정리, §4.8.2. W-13도 대조 후 E 유지, §4.8.3. W-20은 사용처별 B·C·D로 나눠 E에서 뺐다, §4.8.4. W-30은 D로 재분류, §4.12.3. 남은 E 3건) |
| 3 | read-bridge로 되는 것과 안 되는 것 구분 | **완료**: B 6건(W-14 판정, W-20 일부 포함. 2026-10-13 정정: 이전 표기 5건은 W-20 분류 전 값), 불일치 2건(W-13 READY, W-15 집계뿐). 접근 전제 3가지(§4.8, §4.8.1) |
| 4 | cron 미확인 사항 | **완료(미확인 명시)**: 3줄 NOT_VERIFIED, 필요한 비민감 증거 4가지(§4.9) |
| 5 | 승인 보안의 과도한 주장 수정 | **완료**: §4.4, §4.5, §8 D-2, history 기록 |
| 6 | 비운영 문서 테스트 | **완료**: history 기록의 C1 "Tests" 줄 |
| 7 | 실제 작업과 제안 구분 | 이 절 머리의 근거 등급, 각 행의 NOT_VERIFIED |
| 8 | C2 이후 다음 작업 | §6, §8 D-11–D-13 |

C1에서 끝나지 않는 것은 다음과 같다. 대부분 Thomas의 증거나 판단이 필요하다.
- E 3건(W-11–W-13)
- cron 3줄
- Mac SSH 계정
- 볼트 경로
- 관리 설정 위치

### 4.12 결정 준비: D-11, D-16, D-14 (2026-10-13)

**방법:**
- origin/main `1b45ed57`의 소스·compose·정책만 읽었다.
- 런타임 코드, 정책, 실행기, 컨테이너, 로그, 상태 파일에는 손대지 않았다.
- 이 절은 결정을 준비할 뿐이다. D-11, D-14, D-16은 모두 미결정으로 남는다.

#### 4.12.1 D-11 read-bridge 접근 정책

**지금의 경계 (VERIFIED_FROM_SOURCE)**

| 항목 | 사실 | 근거 |
|---|---|---|
| 도달 | 소켓은 상태 루트의 `bridge/read.sock`. 소켓 0660, 디렉터리 0750, 클라이언트 gid(기본 10000)에게 준다 | `read_bridge.py:50`, `socket_door.py:75-82,665-698` |
| 인증 | `SO_PEERCRED` uid 허용 목록. **비어 있으면 그룹만 본다**(설계상 opt-in). 설정됐는데 확인할 수 없으면 거부한다. pipeline-worker만 허용 목록을 필수로 강제한다 | `socket_door.py:459-487`, `pipeline_worker.py:723-728` |
| 허용 목록 값 | **변수 하나**(`MVP_BRIDGE_CLIENT_UID`)가 read(`:488`)·dispatch(`:547`)·knowledge(`:732`)·switch(`:798`)에 들어간다. 기본값 10000. pipeline-worker만 리터럴 `"10001"`(`:638`) | `docker-compose.yml` |
| 인가 | 닫힌 읽기 15개. 정책 `control_channel.assistant_read`(actor `assistant_bridge`, `mutation_allowed: false`)가 모두 허가한다 | `read_bridge.py:63-91`, `GOVERNANCE_POLICY.yaml:347-372` |
| 상태 루트 마운트 | 읽기·쓰기(`:ro` 아님) | `docker-compose.yml:446` |
| 감사 | 읽기는 아무것도 기록하지 않는다. 응답과 거부에는 요청자 uid가 남지 않는다 | `read_bridge.py:46-49` |
| 소켓 경로 변경 | `--socket` 또는 `MVP_READ_BRIDGE_SOCKET`. 코드 변경 없이 다른 경로에 인스턴스를 띄울 수 있다 | `read_bridge_cli.py:31-38`, `socket_door.py:718-725` |

**B 판정에 쓰이는 읽기의 노출 범위:**
- **`heartbeat`:** lane 3개의 상태와 설명 문구(`store_reads.py:55-58,130-140`).
- **`runtime_status`:** 제어 상태의 요약.
- **`scheduler_events`:**
  - 텍스트 응답에서는 `status`를 잘라서 보여 준다(`_clip`, `store_reads.py:71-77`).
  - **`data.events`에는 이벤트 원본이 잘리지 않고 들어간다.** 필드는 `schedule_id`, `kind`, `status`, `schedule_run_id`, 그리고 각 fire가 붙이는 추가 필드다(`scheduler.py:842-858`).
  - 실패한 fire의 `status`에 어떤 문구가 들어가는지는 kind별로 확인하지 않았다(NOT_VERIFIED). holdings fire에는 금액이 없다는 이전 기록이 있지만 다른 kind는 미확인이다.
  - hermes가 이미 같은 데이터를 본다는 것은 사정일 뿐 근거가 아니다.

**대안**

| 대안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| (a) 공유 door에 Claude uid 추가 | `.env`의 허용 목록에 Claude uid 추가, gid 10000 가입, `bridge/` bind mount | 새 서비스 없음 | **네 door가 모두 열린다.** switch(런타임 정지·시작), dispatch(분석 실행, workflow, 위임 범위의 스케줄 변경), knowledge가 함께 열리므로 §4.8.2의 결론과 충돌한다. **철회** |
| (b) 지나가기 ACL | `/root`와 상태 루트에 x | 서비스 변경 없음 | 상태 루트의 다른 파일은 모드만으로 지켜진다. 권고하지 않음 |
| (c) 접근 없음 | 해당 읽기는 Thomas가 한다 | 권한 확대 0 | B 판정 6건(W-06 일부, W-14 일부, W-16–W-18, W-20 일부. 2026-10-13 정정: 이전 표기 4건)을 Claude가 쓰지 못한다 |
| **(d) Claude 전용 read-bridge 인스턴스** | 같은 이미지·같은 코드로 서비스를 하나 더 둔다. `--socket`은 Claude 그룹 소유의 호스트 디렉터리(예: `/run/thomas-claude/read.sock`)이고, 허용 목록은 그 서비스의 리터럴 `"<claude uid>"`(pipeline-worker 방식) | Claude는 다른 door에 닿지 않는다. hermes 경계를 건드리지 않는다. 읽기 목록은 같은 닫힌 15개 | compose 서비스 추가와 `test_deployment_env_passthrough.py` 고정 변경(거버넌스). 컨테이너가 하나 는다. 정책 actor 문제가 남는다(아래) |

**권고: (d).**
- 근거가 부족하면 (c)로 둔다. 권한을 넓히지 않는다.
- (a)는 철회한다.

**결정이 필요한 것 (D-11 아래):**
1. **정책 actor:**
   - `assistant_read`의 actor는 `assistant_bridge`다.
   - Claude 읽기를 같은 조항으로 볼지, Claude용 조항을 따로 둘지는 거버넌스 결정이다.
   - read-bridge 코드는 행위자를 상수 `ASSISTANT_ACTOR`로 쓴다(`read_bridge.py:46-49`).
2. **허용 목록 필수화:**
   - (d)의 서비스가 허용 목록 없이 뜨면 그룹만 본다. 이것은 fail-open에 가깝다.
   - 리터럴 고정과 고정 테스트로 막을지, pipeline-worker처럼 코드로 필수화할지 정한다. 후자는 런타임 변경이다.
3. **감사:**
   - 읽기는 지금 기록되지 않는다. Claude 읽기에 요청 로그(uid, 동작, 시각)가 필요한지 정한다. 필요하면 런타임 변경이다.
4. **상태 루트 읽기 전용 마운트:**
   - (d)의 인스턴스를 `:ro`로 마운트할 수 있는지는 NOT_VERIFIED다. 읽기가 잠금 파일을 만드는지 확인해야 한다.

**fail-closed 동작 (VERIFIED_FROM_SOURCE):**
- 모르는 읽기: `VERB_NOT_PERMITTED`
- 정책 미허가: `CONTROL_VERB_NOT_GRANTED`
- uid 미허가: `PEER_NOT_PERMITTED`
- peer 자격을 확인할 수 없을 때: `PEER_CREDENTIALS_UNAVAILABLE`
- 소켓이 없을 때: 클라이언트 쪽 `DOOR_UNREACHABLE`(`socket_door.py:789-797`)
- **예외:** 허용 목록이 비면 거부가 아니라 그룹 검사로 내려간다.

**(d) 승인 전 시험 조건:**
- (d) 서비스의 허용 목록이 Claude uid 하나인지 고정한다(compose 리터럴 테스트).
- Claude uid가 다른 door(read 공유, dispatch, switch, knowledge)에서 `PEER_NOT_PERMITTED`인지 확인한다(C6 부정 시험에 추가).
- hermes uid가 (d) 소켓에서 거부되는지 확인한다.
- `scheduler_events`의 `data` 원본 `status`와 추가 필드에 금액·키·경로가 들어가지 않는지 확인한다. 쓰는 곳을 열거하거나 누출 테스트를 둔다.

#### 4.12.2 D-16 기동 설정 배너: 두 대안 비교

| 기준 | 대안 1: 실행기 고정 패턴 | 대안 2: 서비스 상태 파일 + read-bridge 새 읽기 |
|---|---|---|
| 최소 권한 | Docker 접근은 실행기 안에만 있다(이미 배포용으로 가진 권한). 서비스 쪽 변경 없음 | Docker 접근이 필요 없다. 다섯 서비스가 상태 루트에 파일을 하나씩 더 쓴다 |
| 정보 노출 | 로그 원문을 실행기가 읽는다. 돌려주는 것은 일치한 토큰뿐이다. 패턴이 넓으면 다른 줄의 값이 섞일 수 있으므로 패턴은 앵커가 있는 정규식으로 고정한다 | 파일에는 처음부터 정해진 필드만 쓴다. 로그를 거치지 않는다 |
| 감사 | 실행기 감사 기록에 요청자 uid, 동작, 결과, 시각이 남는다(§4.5) | read-bridge 읽기는 기록이 없다(§4.12.1의 감사 결정을 따른다) |
| 무결성 | 패턴과 서비스 목록은 root 소유 설치본에 있다. Claude는 바꾸지 못한다. 로그 자체는 서비스가 쓰므로 서비스가 거짓 배너를 쓰면 통과한다 | 파일은 uid 10001이 쓴다. 같은 uid의 다른 프로세스도 쓸 수 있다. Claude는 쓸 수 없다(분리 후) |
| 최신성 | 마지막 기동 배너다. 로그 보존(10m × 3, 재생성 시 소실, `scheduler.py:2647-2648`)을 넘으면 "일치 없음"이 된다. 실제 보존 값은 NOT_VERIFIED | 서비스가 기동할 때마다 덮어쓴다. 프로세스가 죽은 뒤의 파일은 낡을 수 있으므로 기동 시각을 함께 써야 한다 |
| 유지보수 | 서비스·배너가 바뀌면 실행기 목록을 고친다. 실행기 재설치가 필요하다(Thomas) | 서비스 코드 다섯 곳, 정책 `assistant_read.verbs`, 정책 고정 테스트, read-bridge 읽기 목록을 고친다 |
| 장애 대응 | 일치 없음, 컨테이너 없음, 읽기 실패는 모두 STOP 또는 UNKNOWN이다. PASS로 보지 않는다 | 파일 없음, 형식 오류, 기동 시각 불일치는 UNKNOWN이다 |
| 테스트 | 가짜 로그 텍스트로 패턴 단위 시험, 원문 비반환 시험, 닫힌 목록 고정 시험 | 서비스별 기록 시험, 읽기 스키마 시험, 정책 고정 시험, D-11 접근 시험 |

**권고:** 기동 배너 3종(door uid, worker 플래그, frontdesk)에는 대안 1.
- 근거:
  - 배포 흐름 안에서만 필요한 확인이다.
  - 실행기가 이미 Docker를 가진다.
  - 감사가 남는다.
  - 서비스 코드와 정책을 바꾸지 않는다.
- 한계:
  - 실행기가 생기기 전(D-1, D-3)에는 의미가 없다.
  - 로그 보존을 넘으면 답할 수 없다.
- 실행기가 거절되면 대안 2로 간다. 대안 2는 D-11의 결정을 함께 따른다.
- 두 대안은 기준마다 우열이 갈린다. 보안 하나로 순위를 매기지 않았다.

#### 4.12.3 D-14 잔여 항목

| 항목 | 현재 근거 | 제공되는 것 / 빠진 것 | 권한·운영 위험 | 기존 대체 범위 | 추가 구현 | 필요한 시험·증거 | 판정 |
|---|---|---|---|---|---|---|---|
| W-11 CLI intake | §4.8.2 | dispatch v2가 요청 텍스트, kind 4종, `naver_keywords`를 받는다. 검증 정책 선택, priority, 모델 tier, 쓰기, development는 없다. 요청자 기록이 다르다(CLI: `real_thomas`, door: `assistant_bridge`) | dispatch door에 Claude uid를 넣으면 workflow와 위임 스케줄 변경까지 열린다. 허용 목록 변수가 공유되므로 switch까지 열린다(§4.12.1) | 분석 요청 자체 | Claude 전용 프로필과 v2 전용 경로(C, 선택지 b) | 프로필 기록 시험, v3·스케줄 거부 시험 | **E 유지** |
| W-12 `--naver-keywords` | §4.8.2 | W-11과 같다. Naver 근거 수집 위치는 같은 worker다 | 같음 | 같음 | 같음 | 같음 | **E 유지** |
| W-13 `live_readiness` | §4.8.3 | read-bridge의 `live_entry_possible`은 기록 기반 부분 동등이다. 프로세스 `ready`는 비동등이다 | 진입 가능 여부는 정보이고 승인이 아니다. 주문 경로는 이를 읽지 않는다 | 진입 가능 여부(B, D-11 이후) | 없음(프로세스 `ready`는 D) | 11개 구성요소와 door 거부 조건의 대조(미수행) | **E 유지**(기록 기반으로 충분한지 Thomas 판단) |
| W-30 transcripts·설정 백업 | `harness_backup.sh:57,92-115,236-258` | 루트 변수 하나(`HARNESS_BACKUP_HOST_ROOT`, 기본 `/root`)가 운영 경로와 `.claude` 경로를 함께 정한다 | Claude 홈이 옮겨가면 core는 Claude 설정 5개를 선택 항목으로 조용히 빠뜨린다(`:239-251`, 로그의 `claude-config=n/5`만 바뀜). transcripts는 예전 `/root/.claude/projects`가 남아 있으면 옛 데이터만 백업하고, 없으면 `FAILED rc=2`다(`:114-115`). **백업이 조용히 빠질 수 있다** | 없음 | 스크립트에 Claude 홈용 두 번째 루트 변수. 저장소 사본과 `/root/backups/`의 설치본 모두(설치본 내용은 NOT_VERIFIED) | 두 루트로 만든 합성 디렉터리에서 core·transcripts의 파일 목록 시험. 분리 후 첫 백업 로그의 `claude-config=5/5`와 새 대화 포함 여부 | **D로 재분류**: Claude가 하지 않는 root 작업이다. 스크립트 변경은 C5 점검 항목이고, 미반영이면 C6 중단 조건이다 |

W-30을 옮긴 것은 분류의 정정이다. 결정 D-14의 상태는 미결정 그대로다.

#### 4.12.4 승인 전 검증 (2026-10-13, 2차)

**표시:** VERIFIED(소스·테스트에서 직접 확인) / SOURCE_SUPPORTED(소스가 뒷받침하지만 전부 열거하지 않음) / NOT_VERIFIED. 소스·compose·정책·테스트만 읽었다. 컨테이너, 로그, 운영 이벤트, crontab은 보지 않았다.

**(1) D-11 전용 read-bridge의 격리 가정**

| 질문 | 결과 | 근거 |
|---|---|---|
| 공유 변수 없이 허용 목록을 고정할 수 있나 | **VERIFIED.** 서비스의 `environment:`에 리터럴을 쓰면 된다. 선례가 pipeline-worker다(`MVP_BRIDGE_CLIENT_UID: "10001"`, `MVP_BRIDGE_CLIENT_GID: "10001"`). 코드는 env만 읽는다 | `docker-compose.yml:637-638`, `socket_door.py:569-590` |
| 그룹도 공유되나 | **VERIFIED: 공유된다.** `group_add`도 네 door가 같은 `${MVP_BRIDGE_CLIENT_GID:-10000}`이다. 전용 인스턴스는 uid와 gid를 **둘 다** 리터럴로 둬야 한다. door는 소켓과 디렉터리를 그 gid에 넘기고, 넘기지 못하면 거부한다 | `docker-compose.yml:467,542,727,794`, `socket_door.py:665-698` |
| 다른 door와 분리되나 | **SOURCE_SUPPORTED.** Claude가 닿는 것은 전용 소켓 하나다. 다른 door의 소켓은 상태 루트의 `bridge/`에 있고, 호스트에서는 `/root`(0700)와 상태 루트(0700) 아래라 Claude가 지나갈 수 없다(§2.1). 전용 컨테이너도 상태 루트 전체를 마운트하므로 그 안에서는 다른 소켓이 보인다. 그러나 서버 프로세스이고 Claude가 아니다. 새 호스트 디렉터리의 실제 권한은 NOT_VERIFIED | `docker-compose.yml:446` |
| 상태 루트 쓰기 권한이 격리를 무력화하나 | **SOURCE_SUPPORTED: 직접 무력화하지는 않는다.** 읽기 목록은 닫혀 있고 변경 동작이 없다(테스트 `test_the_table_names_no_mutating_action`, `test_the_store_family_names_only_reads`). 다만 읽기 경로의 결함이 곧 쓰기 가능한 프로세스의 결함이므로 방어선이 하나 적다 | `read_bridge.py:63-91` |
| `:ro` 마운트 | **SOURCE_SUPPORTED: 지금 구조로는 부분적으로만 된다.** `tasks`·`history` 읽기는 `TaskRegistryStore.latest()`가 잠금 파일을 `open("a")`로 연다. 읽기 전용 파일시스템에서는 실패한다. `scheduler_events` tail은 잠금 없이 읽는다. 15개 전체를 `:ro`로 돌리려면 읽기 목록을 줄이거나(코드 변경) 잠금 경로만 쓰기 가능하게 해야 한다. 나머지 읽기별 잠금 사용은 NOT_VERIFIED | `task_registry.py:359-379`, `filelock.py:84-94`, `store.py:467-495` |
| 기존 `assistant_read` 조항과 별도 조항 | **VERIFIED: 코드가 강제하는 것은 기존 조항뿐이다.** read-bridge는 정책 게이트 대상 2개만 `control.granted_read_verbs`로 확인한다(`read_bridge.py:99,171`). 정책의 읽기 목록과 `_READS`는 테스트가 양방향으로 같게 고정한다(`test_the_policy_verbs_and_the_read_doors_inventory_are_the_same_set_both_ways`). 별도 Claude 조항은 코드 변경 없이는 문서일 뿐이다. 권한 범위를 실제로 제한하는 쪽은 기존 조항이다. 다만 행위자를 구분하지는 못한다 | `read_bridge.py:95-99,171`, `GOVERNANCE_POLICY.yaml:352`, `tests/test_policy_assistant_read_clause.py:15` |
| 읽기 감사 기록이 필요한가 | **VERIFIED: 지금은 없다.** door handler는 요청마다 기록을 남기지 않는다. Claude 읽기를 사후에 구분하려면 전용 인스턴스에 요청 로그(uid, 읽기 이름, 시각, 내용 제외)가 필요하다. 이것은 런타임 변경이다 | `socket_door.py:324-382`, `read_bridge.py:46-49` |

**(2) `scheduler_events` 노출 범위**

| 항목 | 결과 | 근거 |
|---|---|---|
| 이벤트를 쓰는 곳 | **VERIFIED:** scheduler 본체 9곳, `scheduler_cli` 2곳, 위임 3곳(`mutation_event` 사용) | `scheduler.py:2389,2630,2685,2702,2731,2736,2769`, `scheduler_cli.py:161,209`, `schedule_delegation.py:305,325,335` |
| 공통 필드 | **VERIFIED:** `record_type`, `action`, `schedule_id`, `kind`, `status`, `created_at`, 무결성 해시. 경우에 따라 `schedule_run_id`, `duration_ms` | `scheduler.py:842-858`, `events.py:21-22` |
| 실패 status | **VERIFIED: 경로에 따라 다르다.** 본 루프는 `failed:<reason_code>` 또는 `failed:UNEXPECTED:<예외 타입>`으로, 메시지 본문이 없다(`scheduler.py:2760-2764`). **factory 자식 프로세스 실패는 `failed:FACTORY_CHILD:<오류 문구 앞 160자>`이고, 그 문구는 `"<예외 타입>: <예외 메시지>"`다.** 예외 메시지 본문(경로, 값)이 그대로 들어갈 수 있다. 자식이 죽으면 `failed:FACTORY_CHILD_DIED:exit=<코드>`다 | `scheduler.py:2296`, `:2466-2475`, `:2760-2764` |
| 성공 status | **SOURCE_SUPPORTED: kind별 자유 형식 요약.** 표본은 다음과 같다. 전체 kind의 내용은 NOT_VERIFIED다 | `scheduler.py:1199-1600` |
| | crypto: `verdict=`, `route=`, `settled=…(nR)`, `opened=방향:전략id`, 계좌 snapshot의 상태 문구. 표본에 USDT 금액은 없다. 거래 활동은 드러난다 | `crypto/cycle.py:1164-1184`, `crypto/account_store.py:162-187` |
| | dispatch_spend: `cost_usd=x.xx/threshold`와 `runs` | `dispatch_spend.py:108-109` |
| | holdings: 상태어와 told 여부. 금액 없음은 이전 기록(REPORTED) | `scheduler.py:1470-1513` |
| 자유 텍스트 | **VERIFIED:** 일정 생성·변경 이벤트는 `request`(일정의 요청 원문)와 `reason`을 추가 필드로 싣는다. 위임 생성도 같다 | `scheduler.py:861-890`, `schedule_delegation.py:294-307` |
| 텍스트와 data 차이 | **VERIFIED:** 텍스트 응답은 `status`를 60자로 자르고, `data.events`는 원본 행 전체를 준다 | `store_reads.py:71-77,101-127`, `:50` |
| 100개 제한 | **VERIFIED:** 최신 N개(기본 20, 최대 100)만 준다. 오프셋이 없어 더 오래된 이벤트는 이 읽기로 보이지 않는다. 양을 줄일 뿐 내용의 민감도는 줄이지 않는다 | `store_reads.py:43-44,101-127`, 테스트 `test_scheduler_events_returns_the_newest_n_and_states_a_clamped_count` |
| 허용 필드 투영 | **필요하다고 판단한다.** 예외 메시지가 들어가는 경로가 소스로 확인됐기 때문이다(위 factory 행). Claude 인스턴스에는 `action`, `kind`, `schedule_id`, `created_at`, `schedule_run_id`, status의 분류(성공/실패 + reason code)만 돌려준다. `request`·`reason`·자유 형식 요약·`FACTORY_CHILD` 문구는 뺀다. 이것은 런타임 변경이다. 필드 제한을 확인하는 테스트는 지금 없다(grep) | — |

**(3) W-30 분류 재검토**

| 질문 | 결과 |
|---|---|
| 처음 E였던 이유 | "경로만 바꾸면 되는지"가 불확실했다(§4.7 원래 행) |
| D의 정의 | "분리 후 Claude가 하지 않음"(§4.7 분류). 대체 경로 없이 Claude가 손을 떼는 작업이다 |
| W-30을 하는 주체 | **SOURCE_SUPPORTED: root.** 스크립트는 `/root/backups/`(0700 root, §2.1)에 설치돼 crontab으로 돈다(`harness_backup.sh:4-5`). 어느 계정의 crontab인지는 crontab을 읽지 않아 NOT_VERIFIED |
| root 작업이라는 이유만으로 D인가 | **아니다.** D가 맞는 것은 Claude가 하던 일이 아니고 분리 뒤에도 하지 않기 때문이다. 그러나 D만 적으면 "할 일 없음"으로 읽힐 수 있다. W-30에는 분리 **전에** 해야 할 스크립트 변경이 있다 |
| 결론 | **D 유지, 전제조건 표시.** 행에 "C5 선행 변경, C6 중단 조건"이 이미 적혀 있다. 분류 범례에 "D(전제조건)"의 의미를 덧붙인다. 설치본의 실제 내용과 cron 계정은 NOT_VERIFIED로 남긴다. 집계는 바뀌지 않는다 |

**(4) D-16의 승인 의존성**

| 항목 | 내용 |
|---|---|
| D-1·D-3 상태 | 둘 다 **미결정**(§8). D-1은 대안 선택(실행기를 둘지), D-3은 실행기·배포 동작 유형·응답 스키마의 Reuse-first 예외다 |
| 왜 의존하나 | 대안 1은 "실행기 사후 확인의 고정 패턴"이다. 실행기가 없으면 Docker 로그를 읽을 주체가 없다. 실행기를 새로 만드는 것은 CLAUDE.md Guardrail의 새 구성요소이므로 D-3 없이는 코드를 쓸 수 없다. 실행기 계정(D-10)과 읽기 동작의 승인 여부(D-4)도 같은 동작에 걸린다 |
| D-1·D-3 없이 할 수 있는 것 | 문서 수준 명세(서비스·패턴·반환 필드의 닫힌 목록, fail-closed 표, 시험 명세)까지. 코드, 테스트 코드, 설치는 하지 않는다 |
| 설계와 구현의 경계 | 설계: 이 문서의 목록·표·명세. 구현: C3의 실행기 코드와 합성 테스트(D-1, D-3 승인 뒤). 설치·활성화는 C6(별도 승인) |
| fail-closed 조건 | 컨테이너 없음 → STOP. 로그 읽기 실패·시간 초과 → STOP. 패턴 불일치(배너 없음, 보존 기간 초과 포함) → UNKNOWN이고 PASS로 보지 않으며, 사후 확인 전체는 통과하지 못한다. 같은 컨테이너에서 서로 다른 값이 둘 이상 일치 → 가장 최근 기동의 것만 쓰고, 기동 시각을 판별할 수 없으면 UNKNOWN. 예상과 다른 값(예: `uids=unrestricted`) → STOP |
| 감사·비노출 | 실행기 감사 기록에 요청자 uid, 동작, 결과(PASS/STOP/UNKNOWN), 일치한 토큰, 시각을 남긴다. 원문 줄, 주변 줄, traceback은 응답과 감사 어디에도 남기지 않는다. 패턴은 앵커가 있는 정규식이고, 토큰 길이 상한을 둔다 |

#### 4.12.5 scheduler 정보 노출과 D-11 승인 조건 (2026-10-13, 3차)

**표시:** VERIFIED / SOURCE_SUPPORTED / NOT_VERIFIED(§4.12.4와 같음). 소스와 테스트만 읽었다. 운영 이벤트, 로그, 알림 내용은 보지 않았다. 아래 응답 계약과 테스트는 **문서 명세**이고, 코드는 바꾸지 않았다.

**(1) factory 실패 문구의 생성과 흐름 (VERIFIED)**

- **생성:**
  - factory 자식 프로세스는 계산 함수에서 난 모든 예외를 `{"ok": false, "error": "<예외 타입>: <예외 메시지>"}`로 결과 파일에 쓴다(`scheduler.py:2285-2300`).
  - 부모는 그 결과 파일을 읽어 `status = "failed:FACTORY_CHILD:" + error[:160]`를 만든다(`:2466-2471`).
  - 다른 실패 status는 고정 코드다: `FACTORY_CHILD_DIED:exit=<n>`, `FACTORY_CHILD_TIMEOUT`, `FACTORY_CHILD_ORPHANED`, 손상 결과의 `CORRUPT_RESULT`(`:2432`, `:2466`, `:2475`, `:2480`).
- **한 status가 네 곳으로 간다:** `_finish_factory_occurrence`(`:2384-2397`)를 거친다.

| # | 목적지 | 소비자 | 자르기 | 근거 |
|---|---|---|---|---|
| F1 | scheduler 이벤트 원장(`runtime_ledger/scheduler_events.jsonl`) | read-bridge `scheduler_events`(hermes, D-11이면 Claude). `data.events`는 원본 행 | **없음**(텍스트 응답만 60자) | `store_reads.py:101-127`, `read_bridge.py:210` |
| | | `scheduler_cli`의 중단 실행 검사 | 해당 없음(짝 맞추기만) | `scheduler_cli.py:199` |
| | | `deploy_preflight`(운영 preflight) | 출력 없음(kind·id 끝 8자·시각만) | `deploy_preflight.py:322-375` |
| F2 | 일정의 `last_status`(`schedules.jsonl`) | read-bridge `schedules`: `data`와 텍스트 모두 60자로 자름 | 60자. 접두사 21자를 빼면 메시지 앞 약 39자 | `store_reads.py:78-98`, `scheduler.py:731,753-755` |
| | | `scheduler_cli list`(운영자) | 없음 | `scheduler_cli.py:304-305` |
| | | 일정을 지우면 변경 이벤트가 `last_status`를 실어 F1로 다시 간다 | 없음 | `scheduler.py:886-888` |
| | | crypto data review·cycle은 마커 문자열을 읽는다(판정용) | 해당 없음 | `crypto/data_review.py:534-536`, `crypto/cycle.py:1249` |
| F3 | 운영자 실패 알림 | `build_alerter` → 운영자 등록 채널(Thomas). 본문에 `status:` 원문 | 없음 | `scheduler.py:787-815`, `scheduler_cli.py:99-112` |
| F4 | scheduler 서비스 stderr | 서비스 로그(`docker logs`, W-20) | 없음 | `scheduler_cli.py:469-470` |

**민감 정보 가능성:**
- **SOURCE_SUPPORTED:** 예외 메시지는 계산 코드가 던진 문자열 그대로다. 그래서 다음이 들어갈 수 있다.
  - 파일 경로(예: `FileNotFoundError`)
  - 데이터 값(예: `ValueError` 문구)
  - 기호·전략 id
- **NOT_VERIFIED:**
  - factory 계산 경로가 키나 env 값을 예외 문구에 넣는 곳이 있는지는 확인하지 않았다.
  - 자식 프로세스는 scheduler에서 fork되므로 scheduler의 env를 물려받는다(fork 동작에서 추론).
- **일정 변경·위임 이벤트의 자유 텍스트:** 이 이벤트들은 다음을 싣는다(VERIFIED, `scheduler.py:861-890`, `schedule_delegation.py:184-189`).
  - 일정 요청 원문(`request`)과 `reason`. 분석 요청 텍스트이고 작성자가 쓴 그대로다.
  - 제안 이벤트의 `proposed_by`, `out_of_scope`.

**기존 테스트가 보장하는 것:**
- `test_a_child_that_raises_is_a_terminal_failed_with_no_candidates`는 접두사와 예외 타입만 확인한다(`tests/test_mvp_runtime_scheduler_factory_child.py:125-139`).
- 다른 세 테스트는 고정 코드를 확인한다(`:186-234`).
- 160자 상한, 메시지 내용, 노출 경로를 확인하는 테스트는 없다(grep).
- read-bridge 쪽은 개수 제한 테스트뿐이다(`tests/test_mvp_runtime_read_bridge.py:363`).

**별도 감사 이슈 (Option C와 무관):**
- hermes 컨테이너는 이미 F1(원문)과 F2(60자)로 이 문구를 받는다.
- 정밀화(2026-10-13): 저장소의 hermes shim은 `scheduler_events`·`schedules`를 `with_data` 없이 부른다(`integrations/hermes/mcp/read_bridge_mcp.py:74-86`, `:161-165`, `:169-173`). 그래서 **hermes 모델이 보는 것은 60자로 잘린 `reply`뿐**이다(실패 문구는 접두사를 빼고 약 39자). `data` 원본은 shim 프로세스(hermes 컨테이너)까지만 간다. 설치본 shim이 저장소 사본과 같은지는 NOT_VERIFIED다(`scripts/ops/install_hermes_shims.sh`가 복사한다).
- 정규화(아래 N-규칙)를 원본 기록 쪽에 둘지, Claude 인스턴스의 응답에만 둘지는 결정이 필요하다(D-11 하위 결정 e).

**(2) Claude용 최소 응답 계약 (명세)** — 2026-10-13 §4.13이 이 초안을 대신하는 구현 명세다. 아래는 이력으로 남긴다.

원칙:
- 원본 이벤트 객체를 돌려주지 않는다.
- 아래 표의 필드만 새로 만들어 돌려준다.
- 모든 값은 형식을 검사한다. 통과하지 못한 값은 버리고 그 사실만 표시한다.

| 필드 | 허용 값 | 출처 | 규칙 |
|---|---|---|---|
| `action` | 닫힌 목록: `started`, `fired`, `failed`, `abandoned`, `skipped`, `deferred`, `gap_detected`, `created`, `enabled`, `disabled`, `removed`, `expired`, `proposed`(`scheduler.py:73-103`의 `ACTION_*`와 run 동작) | `action` | 목록 밖이면 `UNKNOWN` |
| `kind` | 닫힌 목록: `scheduler.py`의 `KIND_*` 17개(`analysis_task`부터 `holdings_refresh`까지) | `kind` | 목록 밖이면 `UNKNOWN`. 이때 `outcome`은 `UNKNOWN`으로 둔다 |
| `schedule_id`, `schedule_run_id` | `^[A-Za-z0-9_:.-]{1,80}$` | 같은 이름 | 형식 밖이면 버린다 |
| `created_at`, `started_at` | ISO 8601 UTC | 같은 이름 | 파싱 실패면 버린다 |
| `duration_ms`, `interval_seconds`, `pass_elapsed_ms`, `pass_budget_ms` | 0 이상 정수 | 같은 이름 | 정수가 아니면 버린다 |
| `outcome` | 아래 N-규칙으로 만든 값 | `action` + `status` | 원본 `status`는 돌려주지 않는다 |
| `previously_enabled` | bool | 같은 이름 | — |
| `change_action` | `create`, `enable`, `disable`, `remove` | 제안 이벤트 | 목록 밖이면 버린다 |
| `out_of_scope` | reason code 목록, 각 `^[A-Z][A-Z0-9_]{2,63}$`, 최대 10개 | 제안 이벤트 | 형식 밖 항목은 버린다 |
| `projection` | `"v1"`, 버린 필드가 있으면 `"v1-partial"` | — | 소비자가 손실을 알 수 있게 한다 |

**N-규칙 (`outcome`):**
- `fired`: `"fired"`. kind별 요약 문구는 버린다. kind별로 허용할 요약 필드는 **지금 0개**이며, 추가하려면 그 kind의 status 문구를 열거하고 승인받는다.
- `failed`: `failed:` 다음의 첫 토큰이 `^[A-Z][A-Z0-9_]{2,63}$`이면 `"failed:" + 토큰`이고, 나머지는 버린다. 따라서 `FACTORY_CHILD:<문구>`는 `failed:FACTORY_CHILD`, `UNEXPECTED:<타입>`은 `failed:UNEXPECTED`, `FACTORY_CHILD_DIED:exit=1`은 `failed:FACTORY_CHILD_DIED`가 된다. 토큰이 형식 밖이면 `failed:UNKNOWN`.
- `skipped`·`deferred`: 닫힌 목록 값(`deferred_pass_budget`, `deferred_factory_child_running`, 코드에서 열거한 skip 값). 목록 밖이면 `"<action>:OTHER"`. skip 값 전체 열거는 구현 시 할 일이다(NOT_VERIFIED).
- `gap_detected`: `overdue_seconds` 정수만.
- `abandoned`: `"abandoned_mid_run"`.
- 변경: `created`, `enabled`, `disabled`, `removed`, `expired`(변경 이벤트의 status는 `_MUTATION_STATUS` 값으로 제한).
- 제안: `"proposed"`.

**항상 빼는 필드:**
- `request`, `reason`, `last_status`, `last_run_at`(제거 이벤트), `proposed_by`
- 원본 `status`
- `record_type`, `integrity`
- 목록에 없는 모든 필드

**`schedules` 읽기:** 같은 원칙으로 `last_status` 대신 `last_outcome`(N-규칙)만 준다.

**기존 구조와 맞지 않는 점:**
- 현재 read-bridge는 hermes와 Claude가 같은 코드를 쓰고, `data.events`가 원본 행이다. 투영을 Claude 인스턴스에만 적용하려면 인스턴스별 설정(예: 투영 모드 플래그)이 필요하다. 모두에 적용하면 hermes 쪽 계약이 바뀐다.
- 텍스트 응답(`reply`)도 원본 `status`를 60자로 보여 준다. Claude 인스턴스에서는 `reply`도 투영된 값으로 만들어야 한다.
- 원본 `status`를 응답 직전에 정규화해도 원장에는 원문이 남는다. 원장 쪽 정규화는 별도 결정이다(e).

**(3) D-11 승인 조건**

| 구분 | 조건 | 근거 |
|---|---|---|
| **필수 승인 조건** (Thomas 결정, 구현 전) | a. (d) 전용 인스턴스 채택 | §4.12.1 |
| | b. 정책 actor: 기존 `assistant_read` 조항(코드가 강제)으로 볼지, 별도 조항(코드 변경 필요)을 둘지 | §4.12.4 |
| | c. `scheduler_events`·`schedules` 투영(위 계약) 채택. **투영 없이는 Claude를 받지 않는다** | 이 절 (1) |
| | d. 요청별 감사 기록 채택과, 기록 실패 시 동작(권고: 읽기 거부) | §4.12.4 |
| | e. 원장(`scheduler_events.jsonl`, `last_status`)에 **저장되는** factory 실패 문구를 정규화할지. 응답 투영(c)과 별개이고, hermes를 포함한 모든 원장 소비자에 영향(별도 감사 이슈) | 이 절 (1), §4.14 e |
| | f. Claude 인스턴스의 읽기 목록을 필요한 6개로 줄이고 상태 루트를 `:ro`로 둘지(f2), 15개와 쓰기 가능 마운트(f1)를 받아들일지. 15개 전체는 `tasks`·`history`의 잠금 쓰기 때문에 `:ro`로 돌릴 수 없다 | §4.12.4, §4.14 f |
| **구현 조건** (C3 범위, 승인 뒤) | 서비스별 리터럴 uid·gid(공유 변수 사용 금지)와 그 고정 테스트 | `docker-compose.yml:637-638` 선례 |
| | 허용 목록이 비면 listen 거부(pipeline-worker 방식) | `pipeline_worker.py:723-728` |
| | 투영 모드와 투영 테스트(아래 X-1–X-8) | 이 절 (2) |
| | 요청 로그: 요청자 uid, 읽기 이름, 시각, 결과 코드. 내용은 남기지 않는다 | — |
| | 소켓 경로는 `bridge/` 밖. hermes 마운트와 겹치지 않음을 테스트로 고정 | `test_deployment_env_passthrough.py` |
| **운영 검증 조건** (C6, 별도 승인) | 호스트 소켓 디렉터리가 0750이고 Claude 전용 그룹이다. 다른 uid는 연결이 거부된다 | — |
| | Claude uid가 공유 read·dispatch·switch·knowledge door에서 `PEER_NOT_PERMITTED`. hermes uid가 Claude 소켓에서 거부 | — |
| | 기동 배너 `uids=[<claude uid>]`(D-16 경로로 확인) | `socket_door.py:701-714` |
| | 요청 로그가 실제로 쌓이는지, 기록 실패 시 거부되는지 | — |
| | 투영 결과에 원문 문구가 없는지. Claude가 원본을 읽어 대조하는 방식은 쓰지 않는다. Thomas가 보거나 합성 이벤트로 확인한다 | — |

**의존성:**
- c가 정해지지 않으면 a를 승인해도 Claude를 받을 수 없다. 원문 예외 문구가 그대로 노출되기 때문이다.
- d와 f는 a의 구현 방식을 바꾼다.
- b와 e는 a와 독립적으로 정할 수 있다.

**(4) 테스트 명세 (기존 테스트 구조에서 가능한 범위)** — 초안. 확정판은 §4.13.7(X-0–X-12)이고, 아래는 이력으로 남긴다.

모두 합성 데이터만 쓴다. `tmp_path`의 `LedgerStore`·`ScheduleStore`와 `apply_read`를 직접 부른다. 이 방식은 이미 `test_mvp_runtime_read_bridge.py`가 쓴다. 운영 환경은 바꾸지 않는다.

| # | 상황 | 준비 | 기대 |
|---|---|---|---|
| X-1 | 예외 메시지에 민감 문자열 | factory 실패 이벤트의 status에 `failed:FACTORY_CHILD:FileNotFoundError: /root/secret/path TOKEN=abc` 기록 | 응답 `outcome == "failed:FACTORY_CHILD"`. 응답 JSON 전체(`reply` 포함)에 `/root`, `TOKEN`, `abc`가 없다 |
| X-2 | 허용 목록 밖 필드 추가 | 이벤트에 `request`, `reason`, `api_key`, `foo` 추가 | 응답 행의 키 집합 ⊆ 허용 목록. 추가 필드는 어디에도 없다 |
| X-3 | 알 수 없는 kind | `kind="not_a_kind"` | `kind == "UNKNOWN"`, `outcome == "UNKNOWN"`, `projection == "v1-partial"` |
| X-4 | 잘못된 데이터 형식 | `created_at="yesterday"`, `duration_ms="5s"`, `out_of_scope=["ok", "lower case"]` | 해당 필드가 빠지고 `projection == "v1-partial"`. 행은 남는다 |
| X-5 | 읽기 실패·접근 거부 | 원장 파일 읽기 실패를 주입하고, 허용 목록 밖 uid로 연결 | 타입이 있는 거부(`SCHEDULER_EVENTS_UNAVAILABLE` 계열, `PEER_NOT_PERMITTED`). 원문 일부도 응답에 없다 |
| X-6 | 요청 로그 기록 실패 | 로그 경로 쓰기 실패를 주입 | 읽기를 거부한다(d를 그렇게 정했을 때). 응답에 데이터가 없다 |
| X-7 | 예상 못 한 필드가 응답에 섞임 | 투영 함수 출력에 키를 하나 더하는 결함을 주입 | 응답 직전의 닫힌 스키마 검증이 거부한다(`REPLY_SCHEMA_INVALID`, §4.3과 같은 장치) |
| X-8 | `schedules` 읽기 | `last_status`가 X-1과 같은 일정 | `last_outcome == "failed:FACTORY_CHILD"`. 원문 없음 |
| X-9 | 성공 요약 | crypto fire의 `verdict=…opened=long:s1…` status | `outcome == "fired"`. 요약 문구 없음 |
| X-10 | 회귀: hermes 인스턴스 | 투영 모드가 꺼진 인스턴스 | 기존 테스트가 그대로 통과(투영이 Claude 인스턴스에만 걸림) |

**미검증 (NOT_VERIFIED):**
- factory 계산 코드가 예외 문구에 env·키 값을 넣는 곳이 있는지
- skip status 값의 전체 목록
- hermes 쪽 소비자가 `data.events`의 원본 필드에 의존하는지. 저장소 shim은 의존하지 않는다(2026-10-13, §4.14 c). 설치본은 미확인
- 운영 알림(F3)과 서비스 로그(F4)의 실제 내용

### 4.13 scheduler 읽기 응답 계약 v1 (구현 명세, 2026-10-13)

이 절은 §4.12.5 (2)의 초안을 대신하는 **구현 명세**다. 구현자가 추가 질문 없이 쓸 수 있도록 필드, 타입, 허용 값, 실패 동작을 정한다.

- 저장소 코드는 바꾸지 않았다. 계약을 쓰는 것은 D-11 c의 승인 뒤 C3다.
- 아래 열거값은 origin/main `1b45ed57`의 상수와 대조했다(§4.13.6).

#### 4.13.1 적용 범위와 원칙

- **적용 대상:** D-11 (d)의 Claude 전용 read-bridge 인스턴스의 `scheduler_events`와 `schedules` 두 읽기. 인스턴스별 "투영 모드"로 켠다. hermes 인스턴스의 기존 응답은 바꾸지 않는다(D-11 e가 정해지기 전까지).
- **원칙:**
  1. 원본 이벤트·일정 객체를 돌려주지 않는다. 응답 행은 아래 허용 필드만으로 **새로 만든다**.
  2. 값마다 형식을 검사한다. 통과하지 못한 값은 버리고, 행에 `projection: "v1-partial"`을 붙인다. 원래 값은 어디에도 남기지 않는다.
  3. `reply`(텍스트)는 투영된 행으로만 렌더링한다. 원본에서 텍스트를 만들지 않는다.
  4. 응답 직전에 닫힌 JSON Schema(§4.13.4)로 `data`를 검증한다. 실패하면 응답 대신 `REPLY_SCHEMA_INVALID`로 거부한다. 원문은 거부 사유에도 넣지 않는다.

#### 4.13.2 `scheduler_events` 행

| 필드 | 필수 | 타입·허용 값 | 출처와 규칙 | 근거 |
|---|---|---|---|---|
| `action` | 예 | 13개 중 하나: `started` `fired` `failed` `abandoned` `skipped` `deferred` `gap_detected` `created` `enabled` `disabled` `removed` `expired` `proposed`, 또는 `UNKNOWN` | 원본 `action`. 목록 밖이면 `UNKNOWN` | `scheduler.py:73-103`, `:846` |
| `kind` | 예 | `scheduler.KINDS` 17개 중 하나 또는 `UNKNOWN` | 원본 `kind`. 목록 밖이면 `UNKNOWN` | `scheduler.py` `KIND_*`, `KINDS` |
| `outcome` | 예 | 아래 N-규칙의 값, 최대 72자 | 원본 `status` + `action`. 원본 `status`는 돌려주지 않는다 | §4.13.3 |
| `projection` | 예 | `v1` / `v1-partial` | 아래 정의 | — |
| `schedule_id` | 아니오 | `^schedule_[0-9a-f]{20}$` | `integrity.short_id("schedule", …)` | `scheduler.py:552-555`, `read_only_kernel/integrity.py:115-117` |
| `schedule_run_id` | 아니오 | `^srun_[0-9a-f]{20}$` | `schedule_run_id()` | `scheduler.py:833-839` |
| `created_at`, `started_at` | 아니오 | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | `timeutil.utc_now_iso` 형식. `started_at`은 abandoned 이벤트 | `timeutil.py:15,25-27`, `scheduler.py:899-902` |
| `duration_ms`, `interval_seconds`, `pass_elapsed_ms`, `pass_budget_ms` | 아니오 | 0 이상 정수(bool 제외) | 같은 이름 | `scheduler.py:2389-2390`, `:2685-2687`, `schedule_delegation.py:188` |
| `overdue_seconds` | 아니오 | 0 이상 정수 | `gap_detected`의 status `overdue_seconds=<n>`에서만 파싱 | `scheduler_cli.py:161-162` |
| `previously_enabled` | 아니오 | bool | 변경 이벤트 | `scheduler.py:884-885` |
| `change_action` | 아니오 | `create` `enable` `disable` `remove` | 제안 이벤트에서만 | `schedule_delegation.py:36,187` |
| `out_of_scope` | 아니오 | 문자열 목록, 각 `^[A-Z][A-Z0-9_]{2,63}$`, 최대 10개 | 제안 이벤트에서만. 형식 밖 항목은 버린다 | `schedule_delegation.py:189` |

**항상 빼는 필드:**
- `status`(원본), `request`, `reason`, `last_status`, `last_run_at`(제거 이벤트), `proposed_by`, `record_type`, `integrity`
- 위 표에 없는 모든 키. 앞으로 생길 키도 포함한다.

**`data`:** `{events: [행…], count: int(0–100), active_file_total: int, as_of: 시각, projection: v1|v1-partial}`. 개수 제한은 기존과 같다(기본 20, 최대 100, `store_reads.py:42-43`).

**`projection` 정의:**
- 행이 `v1-partial`이 되는 경우:
  - 허용 목록에 있는 원본 필드가 형식 검사에서 버려짐
  - `action` 또는 `kind`가 `UNKNOWN`
  - `outcome`이 `UNKNOWN`이거나 `…:UNKNOWN`
  - 원본 행이 객체가 아님
- 허용 목록 **밖**의 필드를 버리는 것은 정상이므로 partial이 아니다.
- 응답의 `data.projection`은 행 중 하나라도 partial이면 `v1-partial`이다.

**`reply`:** 행마다 `<created_at|-> <action> <kind> <outcome>` 한 줄이고, 머리 줄에 개수만 쓴다. 투영된 값 밖의 문자는 쓰지 않는다.

#### 4.13.3 `outcome` N-규칙 (원본 `status` → 정규화 값)

| `action` | 원본 `status` | `outcome` | 근거 |
|---|---|---|---|
| `fired` | 무엇이든(kind별 자유 요약) | `fired` | `scheduler.py:1199-1600`. 요약은 버린다. kind별로 허용하는 요약 필드는 0개다 |
| `failed` | `^failed:([A-Z][A-Z0-9_]{2,63})(:…)?$` | `failed:<그 코드>` | 첫 코드 뒤는 모두 버린다. 예: `failed:FACTORY_CHILD:…` → `failed:FACTORY_CHILD`, `failed:UNEXPECTED:ValueError` → `failed:UNEXPECTED`, `failed:FACTORY_CHILD_DIED:exit=1` → `failed:FACTORY_CHILD_DIED`(`scheduler.py:2432,2471,2475,2480,2760-2764`) |
| | 그 밖 | `failed:UNKNOWN` (partial) | — |
| `started` | `running` | `started` | `scheduler.py:2737` |
| `abandoned` | `abandoned_mid_run` | `abandoned` | `scheduler.py:901` |
| `skipped` | `skipped_not_active` | `skipped:not_active` | 유일한 생성 지점 `scheduler.py:2628-2630` |
| `deferred` | `deferred_pass_budget` / `deferred_factory_child_running` | `deferred:pass_budget` / `deferred:factory_child_running` | `scheduler.py:2683,2700` |
| `gap_detected` | `overdue_seconds=<n>` | `gap_detected` + `overdue_seconds` 필드 | `scheduler_cli.py:162` |
| `created` `enabled` `disabled` `removed` `expired` | `enabled` / `disabled` / `removed` | `action` 값 그대로 | `_MUTATION_STATUS`(`scheduler.py:106-111`). `expired`의 status는 `disabled`다 |
| `proposed` | `proposed` | `proposed` | `schedule_delegation.py:184-186` |
| 위 표 밖의 조합 | — | `<action>:UNKNOWN` (partial) | — |
| `action` 또는 `kind`가 `UNKNOWN` | — | `UNKNOWN` (partial) | 알 수 없는 kind의 status 의미는 정하지 않는다 |

**주의:** `skipped_no_ledger`, `skipped_market_data_degraded` 같은 문자열은 `skipped` 이벤트가 아니다. `fired` 이벤트의 요약 status다(`scheduler.py:1213-1936`). 그래서 `fired`로 접힌다.

#### 4.13.4 `schedules` 행

| 필드 | 필수 | 타입·허용 값 | 규칙 |
|---|---|---|---|
| `kind` | 예 | `KINDS` 또는 `UNKNOWN` | — |
| `last_outcome` | 예 | `null` / `fired` / `running` / `failed:<코드>` / `failed:UNKNOWN` / `UNKNOWN` | 원본 `last_status`가 null이면 `null`. `failed:` 접두사면 N-규칙의 failed 행. `factory_child_running`이면 `running`. 문자열이 아니면 `UNKNOWN`. **그 밖의 모든 문자열은 `fired`**: `last_status`는 실행이 끝날 때만 기록되고, 실패가 아니면 성공 요약이다(`scheduler.py:2391,2752,2771`) |
| `schedule_id` | 아니오 | `^schedule_[0-9a-f]{20}$` | — |
| `lane` | 아니오 | `risk` / `maintenance` | `store_reads._lane`(`store_reads.py:62-69`, `scheduler.py:345-346`) |
| `interval_seconds` | 아니오 | 0 이상 정수 | — |
| `next_run_at`, `last_run_at` | 아니오 | 시각 형식 | — |

- **`data`:** `{enabled: [행…], disabled_count: int, as_of, projection}`.
- **빼는 것:** `last_status` 원문, `request`, `reason`, `overdue_seconds`. 지연은 `scheduler_events`의 `gap_detected`로 본다.
- **`reply`:** 행마다 `<kind> <last_outcome|->`이고, 원본 요약 렌더러(`render_schedule_summary`)는 쓰지 않는다.
- **제거된 일정의 재노출:**
  - 일정을 지우면 변경 이벤트가 `last_status`와 `last_run_at`을 싣는다(`scheduler.py:886-888`).
  - 두 필드 모두 §4.13.2의 "항상 빼는 필드"에 들어 있다. 그래서 `removed` 행에는 `outcome: removed`만 남는다.

**JSON Schema (초안, 구현 시 `schemas/`에 둘지는 D-3·Reuse-first 결정을 따른다):**

```json
{"$id": "scheduler_read_projection.v1 (draft)",
 "event_row": {"type": "object", "additionalProperties": false,
   "required": ["action", "kind", "outcome", "projection"],
   "properties": {
     "action": {"enum": ["started","fired","failed","abandoned","skipped","deferred","gap_detected",
                         "created","enabled","disabled","removed","expired","proposed","UNKNOWN"]},
     "kind": {"enum": ["<scheduler.KINDS 17개>", "UNKNOWN"]},
     "outcome": {"type": "string", "maxLength": 72, "pattern":
       "^(fired|started|abandoned|proposed|gap_detected|skipped:not_active|deferred:pass_budget|deferred:factory_child_running|created|enabled|disabled|removed|expired|UNKNOWN|failed:[A-Z][A-Z0-9_]{2,63}|[a-z_]+:UNKNOWN)$"},
     "projection": {"enum": ["v1", "v1-partial"]},
     "schedule_id": {"type": "string", "pattern": "^schedule_[0-9a-f]{20}$"},
     "schedule_run_id": {"type": "string", "pattern": "^srun_[0-9a-f]{20}$"},
     "created_at": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$"},
     "started_at": {"$ref": "#/event_row/properties/created_at"},
     "duration_ms": {"type": "integer", "minimum": 0},
     "interval_seconds": {"type": "integer", "minimum": 0},
     "pass_elapsed_ms": {"type": "integer", "minimum": 0},
     "pass_budget_ms": {"type": "integer", "minimum": 0},
     "overdue_seconds": {"type": "integer", "minimum": 0},
     "previously_enabled": {"type": "boolean"},
     "change_action": {"enum": ["create","enable","disable","remove"]},
     "out_of_scope": {"type": "array", "maxItems": 10,
                      "items": {"type": "string", "pattern": "^[A-Z][A-Z0-9_]{2,63}$"}}}},
 "events_data": {"type": "object", "additionalProperties": false,
   "required": ["events","count","active_file_total","as_of","projection"],
   "properties": {"events": {"type": "array", "maxItems": 100, "items": {"$ref": "#/event_row"}},
                  "count": {"type": "integer", "minimum": 0, "maximum": 100},
                  "active_file_total": {"type": "integer", "minimum": 0},
                  "as_of": {"$ref": "#/event_row/properties/created_at"},
                  "projection": {"enum": ["v1","v1-partial"]}}},
 "schedule_row": {"type": "object", "additionalProperties": false, "required": ["kind","last_outcome"],
   "properties": {"kind": {"$ref": "#/event_row/properties/kind"},
                  "last_outcome": {"anyOf": [{"type": "null"}, {"type": "string",
                    "pattern": "^(fired|running|UNKNOWN|failed:UNKNOWN|failed:[A-Z][A-Z0-9_]{2,63})$"}]},
                  "schedule_id": {"$ref": "#/event_row/properties/schedule_id"},
                  "lane": {"enum": ["risk","maintenance"]},
                  "interval_seconds": {"type": "integer", "minimum": 0},
                  "next_run_at": {"$ref": "#/event_row/properties/created_at"},
                  "last_run_at": {"$ref": "#/event_row/properties/created_at"}}},
 "schedules_data": {"type": "object", "additionalProperties": false,
   "required": ["enabled","disabled_count","as_of","projection"],
   "properties": {"enabled": {"type": "array", "items": {"$ref": "#/schedule_row"}},
                  "disabled_count": {"type": "integer", "minimum": 0},
                  "as_of": {"$ref": "#/event_row/properties/created_at"},
                  "projection": {"enum": ["v1","v1-partial"]}}}}
```

#### 4.13.5 원장 최소화와 응답 최소화는 다른 문제다

| | 응답 투영(이 계약) | 원장 정규화 |
|---|---|---|
| 줄이는 것 | Claude에게 **돌려주는** 정보 | 원장·`last_status`에 **저장되는** 정보 |
| 원문이 남는 곳 | 원장, `schedules.jsonl`, 운영자 알림(F3), 서비스 로그(F4) | 알림과 서비스 로그(코드가 따로 쓰는 경우) |
| 영향 | Claude 인스턴스만 | hermes 응답, 운영자 CLI, `data_review`·`cycle`의 마커 읽기(`crypto/data_review.py:534-536`, `crypto/cycle.py:1249`), 진단 가치 |
| 결정 | D-11 c | D-11 e(별도, hermes 감사 이슈와 함께) |

응답 투영만으로는 "Claude가 원문을 받지 않는다"까지만 보장한다. 원장의 원문은 root, uid 10001, 그리고 같은 원장을 읽는 다른 소비자에게 그대로 남는다.

#### 4.13.6 계약 검증 (저장소 밖 참조 구현, 2026-10-13)

명세가 구현 가능하고 모호하지 않은지 확인하려고, 저장소 밖 scratchpad에 참조 투영 함수, 위 스키마, 합성 시험을 만들어 돌렸다. **저장소 코드가 아니고 커밋 대상도 아니다.** 실제 구현은 C3에서 새로 쓴다.

- worktree 루트에서 실행했다. `runtime`이 worktree를 가리킴을 확인했다.
- 결과: **10 passed**.
  - 소스 대조 1개: 명세의 action 13개가 `scheduler`의 `ACTION_*`와 run 동작의 합집합과 같다. kind 17개가 `scheduler.KINDS`와 같다. 위임 변경 동작 4개, `STATUS_FACTORY_RUNNING`, `_MUTATION_STATUS` 값이 소스와 같다.
  - X 시험 9개.
- **결함 주입 대조:** 투영이 원본 `status`를 다시 싣도록 결함을 넣자 10개 중 8개가 실패했다. 통과한 2개는 이벤트 투영을 쓰지 않는 시험(소스 대조, `schedules`)이다. 시험이 누출을 잡는다는 증거다.
- 모든 민감 문자열은 가짜 표지(`FAKE-SECRET-…`, `/root/fake-state/…`)다. 실제 원장·로그·Secret은 읽지 않았다.

#### 4.13.7 테스트 명세 (구현용, X-1–X-10 확정판)

**공통 조건:**
- **인터페이스:** `read_bridge.apply_read(request, …)`. 투영 모드 인스턴스 인자를 받는다(구현 시 추가).
- **저장소:** `tmp_path` 위의 `LedgerStore`와 `ScheduleStore`에 합성 이벤트를 `append_scheduler_event`로 쓴다.
- **선례:** `tests/test_mvp_runtime_read_bridge.py:363`과 같은 방식이다.
- **누출 검사:** `json.dumps(응답 전체)`에 금지 문자열이 없는지 본다. `reply`와 `data`를 함께 검사한다.

| ID | 입력 fixture | 확인할 출력 | 없어야 할 것 | 기대 | 실패의 보안 의미 |
|---|---|---|---|---|---|
| X-1 | `failed` 이벤트, status `failed:FACTORY_CHILD:FileNotFoundError: /root/fake-state/holdings/x.json token=FAKE-SECRET-7f3a` | `outcome == "failed:FACTORY_CHILD"` | 경로, `FAKE-SECRET`, `FileNotFoundError` | 통과 | 예외 메시지가 Claude에게 간다 |
| X-2 | 같은 이벤트에 `request`, `reason`, `api_key`, `last_status`, `proposed_by`, `foo` 추가 | 행 키 ⊆ 허용 목록 | 추가한 키와 그 값, `status`, `integrity` | 통과 | 새 필드가 기본 노출된다 |
| X-3 | `kind="not_a_kind"` 하나와 `action="teleported"` 하나 | `UNKNOWN`·`UNKNOWN`·`v1-partial`. `data.projection == "v1-partial"` | — | 통과 | 의미를 모르는 status가 통과한다 |
| X-4 | `created_at="yesterday"`, `duration_ms="5s"`, `schedule_id="../../etc"`. 제안 이벤트의 `change_action="explode"`, `out_of_scope=["MIN_INTERVAL","lower case","FAKE-SECRET-7f3a"]`. 문자열 행 1개 | 잘못된 필드만 빠지고 `v1-partial`. 비객체 행은 4필드 `UNKNOWN` 행 | `FAKE-SECRET` | 통과 | 형식 검사 없이 값이 흐른다 |
| X-5a | 원장 없이 연 인스턴스(`ledger=None`) | `SCHEDULER_LEDGER_UNAVAILABLE`(`store_reads.py:107`) | 데이터 | 통과(기존 동작) | 실패가 다른 값으로 대체된다 |
| X-5b | 원장 tail에 깨진 JSON 줄(가짜 표지 포함) | `LEDGER_UNREADABLE` 거부(`store.py:467-482`) | 가짜 표지(거부 문구 `reason` 포함) | 통과. 현재 거부 문구에 줄 내용이 들어가는지는 NOT_VERIFIED이므로 구현 시 확인 | 깨진 줄의 내용이 거부 문구로 샌다 |
| X-5c | 허용 목록 밖 uid로 연결 | `PEER_NOT_PERMITTED` | 허용 목록 값. 거부 문구는 목록을 인용하지 않는다(`socket_door.py:483-487`) | 통과. 기존 `test_an_unpermitted_uid_is_refused`(`test_mvp_runtime_socket_door_hardening.py:377`)를 Claude 인스턴스 리터럴로 재사용 | 다른 uid가 Claude 인스턴스를 읽는다 |
| X-6 | 요청 로그 쓰기 실패 주입(D-11 d가 "거부"일 때) | 응답 `ok: false`, 데이터 없음 | `data` | 통과 | 감사 없이 읽힌다 |
| X-7 | 투영 함수가 키 하나를 더 내는 결함 주입 | `REPLY_SCHEMA_INVALID` 거부 | 그 키 | 통과 | 투영 결함이 그대로 나간다 |
| X-8 | 일정 3개: 실패 `last_status`(가짜 표지 포함), 성공 요약(가짜 표지 포함), 비활성 1개 | `last_outcome == ["failed:FACTORY_CHILD","fired"]`, `disabled_count == 1` | 가짜 표지 | 통과 | 60자 자르기로 원문 앞부분이 나간다(현행) |
| X-8b | `removed` 이벤트에 `last_status`(가짜 표지)·`request`·`reason` | `outcome == "removed"` | 가짜 표지 | 통과 | 일정 삭제로 원문이 재노출된다 |
| X-9 | `fired` 이벤트, status에 전략 id·가짜 표지 | `outcome == "fired"` | 가짜 표지 | 통과 | 성공 요약이 거래 활동을 흘린다 |
| X-9b | 알려진 status 9종(started·abandoned·skipped·deferred 2종·gap·UNEXPECTED·DIED·expired) | 정해진 outcome 목록, `projection == "v1"`, `overdue_seconds == 901` | — | 통과 | 정상 값이 partial로 잘못 표시된다(운영 혼란) |
| X-10 | 투영 모드 꺼짐(hermes 인스턴스) | 기존 `test_mvp_runtime_read_bridge.py` 전체가 그대로 통과 | — | 통과 | hermes 계약이 깨진다 |
| X-0 | 소스 상수 | 명세의 닫힌 목록 = `scheduler`·`schedule_delegation` 상수 | — | 통과 | 새 action·kind가 명세 밖에서 생긴다(이 시험이 실패해야 명세를 고친다) |
| X-11 | f2 인스턴스(읽기 6개 부분집합), 읽기 15개를 차례로 요청 | 6개 `ok: true`, 9개 `VERB_NOT_PERMITTED`. 부분집합 ⊆ `_READS` | 9개 읽기의 데이터 | 통과 | 필요 없는 읽기(`tasks`, `result`, `memory` 등)가 열린다 |
| X-12 | 이벤트 150개, `argument` `"2"` / `"abc"` / `"500"` | `count` 2 / 20 / 100, `active_file_total` 150. 기존 `:363` 시험과 같은 의미 | — | 통과 | 투영이 개수 제한을 깨서 원장을 통째로 내보낸다 |

- **이미 있는 테스트:** 개수 제한(`:363`)과 factory 실패의 접두사·예외 타입(`test_mvp_runtime_scheduler_factory_child.py:125-139`)만 확인한다.
- **위 시험 중 저장소에 있는 것은 없다**(X-5a·X-5c의 바탕이 되는 기존 동작·테스트는 있다). scratchpad에서 돌린 것은 X-1–X-4, X-7–X-9b, X-0에 해당하는 참조 시험이다. X-5a–c, X-6, X-10–X-12는 실제 구현이 있어야 실행할 수 있다.
- **결정에 따라 달라지는 시험:**
  - X-6은 d를 "기록 실패 시 거부"로 정할 때만 둔다.
  - X-11은 f2일 때만 둔다.
  - X-10은 c1(hermes 그대로)일 때의 회귀 시험이다. c2라면 "hermes `reply` 표기가 outcome으로 바뀐다"는 시험으로 바뀐다.
- **중복 점검(2026-10-13):** X-1(문구 누출)과 X-2(필드 누출), X-8(일정)과 X-8b(제거 이벤트), X-9(요약 하나)와 X-9b(정상 값 9종)는 서로 다른 경로를 본다. 겹치는 시험은 없다.

### 4.14 D-11 결정 패킷 (2026-10-13)

Thomas가 소스를 다시 보지 않고 정할 수 있도록 하위 결정 a–f를 한 형식으로 정리했다. **모든 항목은 미결정이다.** "권고"는 승인이 아니다.

**표기 대응:** §4.8과 §4.12.1의 접근 선택지 (a)–(d)는 이 절의 하위 결정 a의 선택지와 다음처럼 대응한다. 하위 결정 a–f의 글자와 섞지 않는다.

| §4.8·§4.12.1 | 이 절 |
|---|---|
| (a) 공유 door에 uid 추가 | a3 |
| (b) 지나가기 ACL | a4 |
| (c) 접근 없음 | a2 |
| (d) 전용 인스턴스 | a1 |

**전제:**
- **c가 정해지기 전에는 Claude에게 read-bridge를 열지 않는다.**
- a를 승인해도 c 없이는 Claude를 받지 않는다.
- 이 패킷의 구현은 C3, 호스트 반영은 C5–C6의 별도 승인이다.

#### a. Claude 전용 read-bridge 인스턴스

| 항목 | 내용 |
|---|---|
| 선택지 | a1 전용 인스턴스(§4.12.1 d) / a2 접근 없음(Thomas가 읽음) / a3 공유 door에 uid 추가(철회) / a4 지나가기 ACL(비권고) |
| 장점·단점 | a1: Claude가 다른 door에 닿지 않고 hermes 경계를 건드리지 않는다. 대신 compose 서비스와 테스트가 하나씩 는다. a2: 권한 확대가 0이지만 B 판정 6건(W-06 일부, W-14 일부, W-16–W-18, W-20 일부)을 쓰지 못한다. a3: switch·dispatch·knowledge까지 열린다. a4: 상태 루트를 모드만으로 지킨다 |
| 의존성 | c(필수, 받기 전), d·f(구현 방식), Claude 계정(C1·C5). b와는 독립 |
| 바뀔 것 | `docker-compose.yml`(서비스 1개: 리터럴 uid·gid, 소켓 `--socket`, `group_add` 리터럴), `tests/test_deployment_env_passthrough.py`(리터럴·경로 고정), 호스트 소켓 디렉터리(운영), `docs/DEPLOYMENT.md` door 점검 절 |
| 테스트 | compose 리터럴 고정(공유 변수 미사용), 소켓 경로가 `bridge/` 밖, hermes 마운트와 겹치지 않음, 허용 목록이 비면 listen 거부 |
| 운영 검증 | 소켓 디렉터리 0750 + Claude 전용 그룹. Claude uid는 다른 네 door에서 `PEER_NOT_PERMITTED`. hermes uid는 이 소켓에서 거부. 배너 `uids=[<claude uid>]` |
| 권고 | **a1** |
| 대안 | a2. 투영(c)이나 감사(d)를 받아들이지 않을 때 |

#### b. 정책 actor

| 항목 | 내용 |
|---|---|
| 선택지 | b1 기존 `assistant_read` 조항으로 읽는다(코드가 강제하는 유일한 조항) / b2 Claude용 별도 조항(정책 블록, 코드가 그 조항을 읽게 변경, 고정 테스트) / b3 b1로 시작하고 다음 정책 버전에서 b2 |
| 장점·단점 | b1: 변경 없음, 강제가 확실하다. 다만 정책 문서만 읽는 사람은 Claude가 이 조항 아래에 있다는 것을 모른다. b2: 행위자가 분명하지만 정책 버전 올림과 코드 변경이 든다. 그 사이 두 조항이 어긋날 위험이 있다 |
| 의존성 | 독립. f로 읽기 목록을 줄이면 b2의 조항 내용이 그 부분집합이 된다 |
| 바뀔 것 | b1: 없음(이 문서에 기록). b2: `governance/GOVERNANCE_POLICY.yaml`, `tests/test_policy_assistant_read_clause.py`, `read_bridge.py`의 정책 확인부, 정책 버전 올림 스크립트(`scripts/ops/policy_bump_*` 선례) |
| 테스트 | b2: 새 조항의 읽기 목록과 인스턴스 부분집합이 양방향으로 같음 |
| 운영 검증 | — |
| 권고 | **b3**(지금은 b1, 다음 정책 버전에서 b2) |
| 대안 | b2 즉시 |

#### c. 응답 필드 투영

| 항목 | 내용 |
|---|---|
| 선택지 | c1 §4.13 계약을 Claude 인스턴스에만 / c2 모든 인스턴스(hermes 포함)에 / c3 투영 없음, 그러면 Claude를 받지 않는다 |
| 장점·단점 | c1: hermes 계약을 그대로 두고 Claude만 최소화한다. 인스턴스 모드 플래그가 하나 는다. c2: 노출을 한 번에 줄인다. 저장소의 hermes shim은 이 두 읽기의 `data`를 쓰지 않고 `reply`만 모델에 넘긴다(`read_bridge_mcp.py:74-86,161-173`, SOURCE_SUPPORTED). 그래서 바뀌는 것은 `reply`의 표기(잘린 status → outcome)뿐이다. 설치본 shim과 hermes skill이 status 문구에 기대는지는 NOT_VERIFIED다. c3: 원문 예외 문구가 노출된다(§4.12.5) |
| 의존성 | a의 전제. **e와는 별개다**: c는 응답(Claude·hermes에게 나가는 값)만 줄이고 원장에 저장된 원문은 그대로 둔다. c2를 골라도 원장 정규화(e)는 따로 정해야 한다 |
| 바뀔 것 | `runtime/mvp_runtime/store_reads.py`(투영 함수: 기존 소유자에 두어 Reuse-first를 지킨다), `read_bridge.py`(모드), `read_bridge_cli.py`(플래그), 새 테스트 파일 |
| 테스트 | §4.13.7 X-0–X-10 |
| 운영 검증 | 합성 이벤트나 Thomas가 확인한다. Claude가 원본을 읽어 대조하지 않는다 |
| 권고 | **c1** |
| 대안 | c2. hermes 쪽 의존이 없다고 확인되면 |

#### d. 요청 감사 기록

| 항목 | 내용 |
|---|---|
| 선택지 | d1 서비스 stderr에 한 줄(요청자 uid, 읽기 이름, 시각, 결과 코드) / d2 전용 쓰기 디렉터리(상태 루트 밖)에 JSONL, 기록 실패 시 읽기 거부 / d3 기록 없음(hermes와 같음) |
| 장점·단점 | d1: 쉽지만 서비스 로그는 10m×3이고 재생성 시 사라진다(`scheduler.py:2647-2648`). d2: 남고 fail-closed지만 쓰기 경로가 하나 는다. d3: 사후에 Claude 읽기를 구분할 수 없다 |
| 의존성 | f와 맞물린다. 상태 루트를 `:ro`로 두면 d2는 상태 루트 밖에 써야 한다 |
| 바뀔 것 | `socket_door.py` 또는 `read_bridge.py`(요청 후 기록 훅), compose(로그 디렉터리 마운트) |
| 테스트 | X-6(기록 실패 → 거부), 기록에 응답 내용이 없음 |
| 운영 검증 | 실제 기록이 쌓임, 디렉터리를 Claude가 쓸 수 없음 |
| 권고 | **d2** |
| 대안 | d1. 이때 기록은 사후 대조용 참고일 뿐이다 |

#### e. 원장 데이터도 정규화할지

| 항목 | 내용 |
|---|---|
| 선택지 | e1 원장에 저장되는 문구를 바꾸지 않음(Claude에게 가는 값은 c가 따로 줄인다) / e2 factory 자식 실패만 원장에 `failed:FACTORY_CHILD:<예외 타입>`으로 쓰고, 메시지는 운영자 전용 진단 위치로 / e3 모든 실패 status 정규화 |
| 장점·단점 | e1: 진단 가치는 유지되지만 hermes와 원장 소비자에게 원문이 남는다. e2: 문제가 확인된 유일한 경로만 막는다. 마커를 읽는 곳(`data_review`, `cycle`)은 성공 경로를 읽으므로 영향이 없다(SOURCE_SUPPORTED). 기존 테스트(`ValueError` 포함 확인)도 그대로 통과한다. e3: 범위가 넓고 영향이 크다 |
| 의존성 | Option C와 독립. hermes 감사 이슈와 함께 정한다 |
| 바뀔 것 | e2: `scheduler.py`의 자식 결과 처리(`:2296`, `:2471`), 운영자 진단 기록 위치, `tests/test_mvp_runtime_scheduler_factory_child.py` |
| 테스트 | 메시지에 가짜 표지를 넣은 자식 실패 → 원장·`last_status`·알림에 표지가 없음(알림에 남길지는 따로 정한다) |
| 운영 검증 | 다음 factory 실패 기록의 형태(Thomas) |
| 권고 | **e2**(별도 감사 항목으로, Option C 승인과 분리) |
| 대안 | e1. 이때는 c1이 Claude 쪽을 막는다 |

#### f. 읽기 범위와 마운트

| 항목 | 내용 |
|---|---|
| 선택지 | f1 15개 전체 + 쓰기 가능 마운트(hermes와 같음) / f2 Claude 인스턴스는 필요한 6개만(`runtime_status`, `heartbeat`, `schedules`, `scheduler_events`, `crypto_readiness`, `holdings_status`) + 상태 루트 `:ro` / f3 15개 + `:ro` + 잠금 파일만 쓰기 가능하게 겹쳐 마운트 |
| 장점·단점 | f1: 바꿀 것이 없다. 다만 Claude가 필요 없는 읽기(`tasks`, `history`, `result`, `memory` 등)까지 받고, 쓰기 가능 프로세스 위에서 돈다. f2: 최소 권한이고 노출도 줄어든다. 인스턴스별 부분집합이라는 코드 변경이 든다. 6개가 `:ro`에서 도는지는 SOURCE_SUPPORTED다: `ScheduleStore.list`(`scheduler.py:607-609`), heartbeat(`heartbeat.py:100` "never writes"), 이벤트 tail, readiness 두 모듈에 잠금·쓰기 호출이 없다. `ControlStore.load`와 holdings view는 확인한 범위에서 잠금이 없다. f3: 구성이 복잡하다 |
| 의존성 | b(조항 내용), d(로그 위치) |
| 바뀔 것 | `read_bridge.py`(인스턴스 부분집합), `read_bridge_cli.py`(플래그), compose(`:ro`) |
| 테스트 | 읽기 전용으로 만든 `tmp_path`에서 6개가 성공하고 나머지 9개는 `VERB_NOT_PERMITTED`. 부분집합 ⊆ `_READS` |
| 운영 검증 | 인스턴스 배너의 `reads=[…]`가 6개, 마운트가 `:ro` |
| 합성 검증 (2026-10-13, 저장소 밖) | 이 세션은 root라 파일 모드로는 읽기 전용을 증명할 수 없다. 그래서 프로세스 안에서 쓰기 계열 호출(`open` 쓰기 모드, 쓰기 플래그 `os.open`, `mkdir`·`rename`·`replace`·`unlink`·`touch`)과 소켓 `connect`를 가로채 기록했다. 합성 상태는 `tmp_path`의 일정 1개, 이벤트 1개, 커밋된 정책 파일 사본이다. 6개 읽기는 모두 `ok: true`였고 쓰기 시도와 연결이 **0건**이었다. 대조군 `tasks`·`history`는 잠금 파일 쓰기를 시도해 걸렸다. **한계:** 상태가 비어 있거나 단순해서, 데이터 모양에 따라서만 실행되는 경로(실제 cycle 기록이 있는 readiness, holdings snapshot이 있는 view)는 덮지 못했다. sqlite·mmap 같은 다른 쓰기 수단은 가로채지 않았다. 그래서 등급은 SOURCE_SUPPORTED + 합성 확인이다. 실제 `:ro` 마운트 검증은 C4·C6 |
| 권고 | **f2** |
| 대안 | f1. 이때 d는 d2를 상태 루트 밖에 둔다 |

**의존 관계 요약:**
- c → a(받기 전 필수)
- f → b(조항 내용)
- f → d(로그 위치)
- e는 독립(감사 항목)

권고 조합은 a1 + b3 + c1 + d2 + e2(별도) + f2다.

#### 4.14.1 하위 결정 검토표 (2026-10-13)

c(응답 투영), e(원장 정규화), f(읽기 범위)는 **서로 다른 결정**이다.

| 결정 | 줄이는 것 | 바꾸는 곳 |
|---|---|---|
| c | Claude에게 나가는 응답 | read-bridge 응답 |
| e | 원장에 저장되는 값 | scheduler의 원장 쓰기 |
| f | Claude가 부를 수 있는 읽기와 마운트 권한 | 인스턴스 설정 |

하나를 정해도 나머지는 정해지지 않는다.

| 결정 | 권고안의 근거 | 고르지 않은 대안의 비용·위험 | Claude 데이터 접근 범위에 미치는 영향 | 구현 전 필수 시험 | 운영에서만 확인할 수 있는 것 | Thomas가 명시적으로 정할 것 |
|---|---|---|---|---|---|---|
| a 전용 인스턴스(a1) | 허용 목록·그룹 변수가 네 door에 공유됨(§4.12.1). 리터럴 선례가 있음(`docker-compose.yml:637-638`) | a2: B 판정 6건을 못 씀(Thomas 부담). a3: switch·dispatch·knowledge까지 열림. a4: 상태 루트를 모드만으로 지킴 | Claude가 닿는 곳이 소켓 하나로 한정된다. 무엇을 읽는지는 c·f가 정한다 | compose 리터럴 고정, 소켓 경로가 `bridge/` 밖, 빈 허용 목록이면 listen 거부, X-5c | 호스트 소켓 디렉터리 권한, 다른 door에서 Claude uid 거부, 배너 `uids=` | 채택 여부. Claude 계정의 uid·gid 값(C1·C5) |
| b 정책 actor(b3) | 지금 코드가 강제하는 조항은 `assistant_read` 하나다(§4.12.4). b2는 정책 버전 올림과 코드 변경이 함께 든다 | b1만 고집: 정책 문서만 보는 사람은 Claude가 이 조항 아래 있다는 것을 모른다. b2 즉시: 변경 범위가 커진다 | 없음. 응답과 읽기 범위는 c·f가 정한다 | b2로 갈 때 조항과 인스턴스 부분집합이 양방향으로 같음 | — | b1로 시작할지, 다음 정책 버전에서 b2로 갈지 |
| c 응답 투영(c1) | 실패 문구에 예외 메시지가 들어가는 경로가 확인됐다(`scheduler.py:2296,2471`). 계약은 소스 대조, 참조 구현 10 passed, 결함 주입 8 fail로 확인했다(§4.13.6) | c3: Claude가 예외 문구·요청 원문·kind별 요약을 받는다. c2: hermes `reply` 표기가 바뀐다(설치본 shim 미확인) | `scheduler_events`·`schedules`에서 Claude가 받는 것이 닫힌 필드와 정규화된 outcome으로 줄어든다. **원장에 저장된 원문은 바뀌지 않는다** | X-0–X-4, X-5a·b, X-7–X-9b, X-10, X-12 | 투영 결과에 원문이 없는지(합성 이벤트나 Thomas가 확인) | c1 / c2 / c3. **c3이면 Claude를 받지 않는다** |
| d 요청 감사(d2) | 지금은 읽기가 기록되지 않는다(`socket_door.py:324-382`). 서비스 로그는 10m×3이고 재생성 시 사라진다 | d1: 기록이 남지 않을 수 있다. d3: Claude 읽기를 사후에 구분할 수 없다 | 없음(기록일 뿐). d2의 기록 실패 시 거부를 고르면 기록되지 않는 읽기는 없다 | X-6, 기록에 응답 내용이 없음 | 기록 디렉터리를 Claude가 쓸 수 없음, 실제 기록 축적 | d1 / d2 / d3, 기록 실패 시 거부할지 |
| e 원장 정규화(e2, 별도) | 확인된 누출 경로는 factory 자식 실패 하나다. 마커를 읽는 곳(`data_review`, `cycle`)은 성공 경로라 영향이 없다(SOURCE_SUPPORTED) | e1: 원장, `last_status`, hermes 쪽(모델은 60자 `reply`만, 컨테이너는 원본), 운영자 CLI에 원문이 남는다. e3: 범위가 넓어 진단 가치가 준다 | **Claude 응답 범위에는 영향이 없다**(그것은 c가 정한다). 원장을 읽는 다른 소비자의 노출이 준다 | 가짜 표지를 넣은 자식 실패 → 원장·`last_status`에 표지 없음. 기존 `test_a_child_that_raises…`(예외 타입 포함)는 그대로 통과 | 다음 실제 factory 실패 기록의 형태(Thomas) | e1 / e2 / e3, 운영자 알림(F3)에 원문을 남길지. Option C 승인과 분리해 정한다 |
| f 읽기 범위(f2) | Claude에게 필요한 읽기는 6개다(B 판정). 6개는 잠금·쓰기 호출이 없고(SOURCE_SUPPORTED), 쓰기 감시 합성 시험에서 0건이었다(§4.14 f) | f1: Claude가 `tasks`·`history`·`result`·`memory` 등 필요 없는 읽기까지 받고, 쓰기 가능 프로세스 위에서 돈다. f3: 구성이 복잡하다 | Claude가 부를 수 있는 읽기가 15개에서 6개로 준다. 상태 루트가 읽기 전용이 된다 | X-11, 읽기 전용 `tmp_path`에서 6개 성공 | 실제 `:ro` 마운트에서 6개가 데이터 모양과 무관하게 동작하는지(데이터 의존 경로는 합성 시험이 못 덮음) | f1 / f2 / f3 |

**Thomas의 명시적 결정 없이는 진행하지 않는 것:**
- a–f 각각의 선택
- Claude 계정의 uid·gid 값
- e의 범위와 운영자 알림 처리
- c3을 고를 경우 Claude를 받지 않는다는 결론

권고는 승인이 아니다.

#### 4.14.2 검증 명령과 예상 결과

**저장소 안 (worktree 루트, 지금 실행할 수 있음):**

| 명령 | 예상 결과 | 확인하는 것 |
|---|---|---|
| `.venv/bin/python scripts/build_proposal_status.py --check` | `OK: docs/proposals/STATUS.md is current` | STATUS.md가 머리 줄과 같음 |
| `.venv/bin/python -m pytest -q tests/test_design_record_lifecycle.py tests/test_build_history_fragments.py tests/test_deployment_env_passthrough.py tests/test_dockerignore_excludes_secrets.py tests/test_claude_local_only_guard.py` | 671 passed(2026-10-13 기준) | 문서 형식, history, env 경계, Guard 회귀 |
| `.venv/bin/python -m pytest -q tests/test_mvp_runtime_read_bridge.py tests/test_mvp_runtime_scheduler_factory_child.py tests/test_policy_assistant_read_clause.py` | 74 passed(2026-10-13 기준) | 명세가 인용하는 기존 동작 |
| `.venv/bin/python scripts/run_repository_release_gate.py --full --check-only` | `PASS … check-only mode` | 거버넌스 검증기 |
| `python -c "import scripts.build_proposal_status as m; print(m.__file__)"` | worktree 경로 | 호스트 venv가 기본 체크아웃을 import하지 않음 |

**저장소 밖 (세션 scratchpad, 커밋 대상 아님):**
- 참조 투영 계약 시험(10 passed), 결함 주입(8 fail), 6개 읽기 쓰기 감시(8 passed), D-16 정규식 대조(ALL OK).
- 세션 임시 공간에 있으므로 보존되지 않는다. C3 구현 때 같은 내용을 저장소 시험(X-0–X-12)으로 새로 쓴다.

**구현 뒤 (C3, 아직 실행할 수 없음):**
- X-0–X-12
- §4.14 a·f의 compose·부분집합 시험
- `test_mvp_runtime_read_bridge.py` 전체 회귀(X-10)

#### 4.14.3 운영 검증 체크리스트 (C6, 별도 승인. Claude는 원문을 읽어 대조하지 않는다)

| # | 확인 | 방법 | 결과 표시 |
|---|---|---|---|
| O-1 | 소켓 디렉터리 0750, 소유 그룹이 Claude 전용 | `stat`(Thomas 또는 C6 절차) | PASS/FAIL |
| O-2 | Claude uid가 공유 read·dispatch·switch·knowledge door에서 `PEER_NOT_PERMITTED` | Claude 계정에서 각 소켓에 연결 시도(N 시험과 함께) | PASS/FAIL/BLOCKED |
| O-3 | hermes uid가 Claude 소켓에서 거부 | hermes 컨테이너에서 연결 시도(Thomas) | PASS/FAIL |
| O-4 | Claude 인스턴스 배너 `uids=[<claude uid>]`, `reads=[6개]` | D-16 P8(실행기) 또는 Thomas | PASS/FAIL/UNKNOWN |
| O-5 | 상태 루트 마운트가 `:ro`(f2일 때) | compose 정의와 실행 중 마운트(Thomas) | PASS/FAIL |
| O-6 | 6개 읽기가 실제 상태에서 `ok`(데이터 의존 경로 포함) | Claude 계정에서 6개 호출, 응답 스키마 검증만 확인 | PASS/FAIL |
| O-7 | 요청 로그가 쌓이고 Claude가 그 디렉터리에 쓸 수 없음(d2일 때) | 로그 확인(Thomas), Claude 쓰기 시도 | PASS/FAIL |
| O-8 | 응답에 원문 문구가 없음 | 합성 실패 이벤트를 넣는 운영 절차는 없다. 실제 실패가 생긴 뒤 Thomas가 원장과 응답을 대조한다 | PASS/FAIL/NOT_TESTED |
| O-9 | e2를 고른 경우 다음 factory 실패의 원장 형태 | Thomas | PASS/FAIL/NOT_TESTED |

#### 4.14.4 근거 등급, 승인 전 금지 사항, 바뀔 파일과 시험 (2026-10-13)

**등급:**
- VERIFIED: 소스·테스트에서 직접 확인
- SOURCE_SUPPORTED: 소스가 뒷받침하지만 전부 열거하지 않음
- SCRATCH: 저장소 밖 합성 검증
- INFERRED: 추론
- OPS_REQUIRED: 운영 환경에서만 확인 가능

| 결정 | 핵심 근거와 등급 | 승인 전 금지 사항 | 선택 시 바뀔 코드·설정 | 선택 시 추가될 시험 |
|---|---|---|---|---|
| a | 허용 목록·그룹 변수가 네 door에 공유된다: VERIFIED(`docker-compose.yml:467,488,542,547,727,732,794,798`). 서비스별 리터럴이 가능하다: VERIFIED(`:637-638`). 소켓 경로를 바꿀 수 있다: VERIFIED(`read_bridge_cli.py:31-38`). 다른 door의 소켓은 호스트의 Claude 계정이 지나갈 수 없다: SOURCE_SUPPORTED(`/root`·상태 루트 0700은 2026-10-12 호스트 메타데이터). 새 소켓 디렉터리 권한: OPS_REQUIRED | `MVP_BRIDGE_CLIENT_UID`·`_GID`에 Claude uid·gid를 넣지 않는다. compose에 서비스를 더하지 않는다 | `docker-compose.yml`(서비스 1개), `docs/DEPLOYMENT.md`(door 점검 절), 호스트 소켓 디렉터리(운영) | `tests/test_deployment_env_passthrough.py`(리터럴·경로·hermes 마운트 비중첩), 빈 허용 목록이면 listen 거부, X-5c |
| b | 코드가 강제하는 조항은 `assistant_read`뿐이다: VERIFIED(`read_bridge.py:99,171`, `test_policy_assistant_read_clause.py:15`) | 정책 파일을 바꾸지 않는다 | b1: 없음. b2: `governance/GOVERNANCE_POLICY.yaml`, `read_bridge.py` 정책 확인부, 정책 버전 올림 | b2: 조항과 인스턴스 부분집합의 양방향 일치(`tests/test_policy_assistant_read_clause.py` 확장) |
| c | 실패 status에 예외 메시지가 들어간다: VERIFIED(`scheduler.py:2296,2466-2471`). `data.events`는 원본 행이다: VERIFIED(`store_reads.py:101-127`). 계약이 구현 가능하고 시험이 누출을 잡는다: SCRATCH(§4.13.6). hermes 저장소 shim은 `reply`만 모델에 준다: SOURCE_SUPPORTED. 설치본은 OPS_REQUIRED | Claude를 어떤 read-bridge에도 받지 않는다. **c는 응답만 다룬다. 원장 변경(e)으로 해석하지 않는다** | `runtime/mvp_runtime/store_reads.py`(투영 함수), `read_bridge.py`(모드), `read_bridge_cli.py`(플래그) | 새 시험 파일: X-0–X-4, X-5a·b, X-7–X-9b, X-10, X-12 |
| d | 요청별 기록이 없다: VERIFIED(`socket_door.py:324-382`). 서비스 로그 보존 10m×3: SOURCE_SUPPORTED(소스 주석 `scheduler.py:2647-2648`), 실제 값은 OPS_REQUIRED | 로그 경로·마운트를 추가하지 않는다 | `socket_door.py` 또는 `read_bridge.py`(기록 훅), `docker-compose.yml`(로그 디렉터리) | X-6, 기록에 응답 내용이 없음을 확인하는 시험 |
| e | factory 자식 실패가 예외 메시지를 원장·`last_status`·알림·stderr로 보낸다: VERIFIED(§4.12.5 F1–F4). 마커를 읽는 곳은 성공 경로라 영향 없음: SOURCE_SUPPORTED. 메시지에 경로·값이 들어갈 수 있음: INFERRED. 키·env 값이 들어가는지: NOT_VERIFIED | 원장·`last_status`·알림의 형식을 바꾸지 않는다. **e는 c의 승인으로 함께 승인되지 않는다** | `runtime/mvp_runtime/scheduler.py`(`:2296`, `:2471`), 운영자 진단 기록 위치 | `tests/test_mvp_runtime_scheduler_factory_child.py`(가짜 표지 → 원장·`last_status`에 없음, 기존 예외 타입 확인은 유지) |
| f | `tasks`·`history`는 잠금 파일을 `open("a")`로 연다: VERIFIED(`task_registry.py:359-379`, `filelock.py:93-94`). 6개 읽기에 잠금·쓰기 호출이 없다: SOURCE_SUPPORTED + SCRATCH(쓰기 감시 0건). 실제 `:ro`에서의 동작과 데이터 의존 경로: OPS_REQUIRED | 마운트·읽기 목록을 바꾸지 않는다. **f는 c·e의 승인으로 함께 승인되지 않는다** | `read_bridge.py`(인스턴스 부분집합), `read_bridge_cli.py`(플래그), `docker-compose.yml`(`:ro`) | X-11, 읽기 전용 `tmp_path`에서 6개 성공 |

**공통 금지:**
- 런타임·테스트·정책·compose·실행기·설정 변경
- Docker·cron·호스트 권한·소켓 변경
- 커밋·push·PR

이 금지는 각 결정이 승인되고 그 결정의 구현 단계(C3, C5)가 따로 승인될 때까지 유지된다.

#### 4.14.5 시험·운영 검증의 단계별 상태 (2026-10-13)

**단계:**
- ① 문서 명세만
- ② 저장소 밖 합성 검증 완료(참조 구현이고, 저장소 구현이 아니다)
- ③ 저장소 구현 후 실행 필요
- ④ 운영 환경 검증 필요

어느 시험도 저장소에서 실행된 적이 없다. 기존 회귀 테스트(671·74 passed)는 기존 동작의 회귀 확인일 뿐이고, 신규 기능의 검증이 아니다.

| 항목 | 단계 | 비고 |
|---|---|---|
| X-0 소스 상수 대조 | ②, ③ | scratchpad 10 passed에 포함 |
| X-1 예외 문구 누출 | ②, ③ | 결함 주입 시 실패 확인 |
| X-2 필드 누출 | ②, ③ | 같음 |
| X-3 알 수 없는 kind·action | ②, ③ | — |
| X-4 형식 오류 | ②, ③ | — |
| X-5a 원장 없음 | ①, ③ | 기존 동작(`SCHEDULER_LEDGER_UNAVAILABLE`)은 있음. 투영 모드 시험은 없음 |
| X-5b 깨진 줄 | ①, ③ | 거부 문구에 줄 내용이 섞이는지 NOT_VERIFIED |
| X-5c peer 거부 | ①, ③ | 기존 `test_an_unpermitted_uid_is_refused`가 공유 메커니즘을 확인. Claude 인스턴스 리터럴 시험은 없음 |
| X-6 기록 실패 시 거부 | ①, ③ | d에 따라 있거나 없음 |
| X-7 닫힌 스키마 거부 | ②, ③ | — |
| X-8 `schedules` | ②, ③ | — |
| X-8b 제거 이벤트 | ②, ③ | — |
| X-9 성공 요약 | ②, ③ | — |
| X-9b 정상 값 9종 | ②, ③ | — |
| X-10 hermes 회귀 | ①, ③ | c1/c2에 따라 내용이 달라짐 |
| X-11 f2 부분집합 | ①, ③ | 6개 읽기의 쓰기 0건 합성 검증(②)은 X-11의 일부(쓰기 없음)만 덮는다. 9개 거부는 미검증 |
| X-12 개수 제한 | ①, ③ | — |
| O-1–O-9 | ④ | 모두 미실행(NOT_TESTED). C6 별도 승인 뒤 |

#### 4.14.6 최종 검토: c1·a1·d2·f2의 근거와 상호 의존성 (2026-10-13)

**권고값 일관성:** 각 권고는 문서 안의 세 곳에서 같은 값이다.
- §4.14 결정 행
- 권고 조합 요약
- §8 D-11 행

§4.12.1의 "(d)"는 a1과 같다.

| 권고 | 한 줄 근거 (등급) | 이 권고가 기대는 것 | 이 권고를 거절하면 |
|---|---|---|---|
| **c1** 응답 투영(Claude 인스턴스만) | 실패 status에 예외 메시지가 들어가고 `data`가 원본 행이다(VERIFIED). 계약은 구현할 수 있고, 시험이 누출을 잡는다(SCRATCH) | 없음. 다른 권고의 **전제** | c2: hermes `reply` 표기가 바뀐다(설치본 shim 미확인). c3: **Claude를 받지 않는다**. 그러면 a1·d2·f2는 구현해도 쓸 데가 없다 |
| **a1** 전용 인스턴스 | 허용 목록·그룹 변수가 네 door에 공유된다(VERIFIED). 리터럴 선례가 있다(VERIFIED) | c(받기 전 필수), Claude 계정 uid·gid(C1·C5) | a2: c·d·f가 모두 쓸 데가 없다(Claude 접근 없음). a3: switch·dispatch·knowledge가 열린다. a4: 상태 루트를 모드만으로 지킨다 |
| **d2** 상태 루트 밖 JSONL, 기록 실패 시 거부 | 지금은 읽기 기록이 없다(VERIFIED). 서비스 로그는 보존이 짧다(SOURCE_SUPPORTED) | a1(기록할 인스턴스). f와 맞물림: 상태 루트가 `:ro`면 로그는 그 밖이어야 하는데, d2는 처음부터 밖이다 | d1: 기록은 생기지만 사라질 수 있다. X-6은 "기록 실패 시 거부"를 고르지 않으면 빠진다. d3: Claude 읽기를 사후에 구분할 수 없다. 받는 것 자체는 가능하다 |
| **f2** 6개 읽기 + `:ro` | 6개 읽기에 잠금·쓰기가 없다(SOURCE_SUPPORTED + SCRATCH). `tasks`·`history`는 잠금 쓰기(VERIFIED) | a1(인스턴스 부분집합), b(b2로 가면 조항 = 6개) | f1: Claude가 15개(`tasks`·`result`·`memory` 포함)를 읽고 쓰기 가능 프로세스 위에서 돈다. 받는 것 자체는 가능하다 |

**의존 관계:**
- c1은 Claude를 받기 위한 **필수 전제**다.
- a1은 d2와 f2가 적용될 **대상**이다.
- d2와 f2는 a1의 **구현 방식**이다. 서로는 독립이지만, 로그 위치는 f를 고려해 상태 루트 밖에 둔다.
- 거절해도 Claude를 받을 수 있는 것은 d와 f뿐이다. c를 거절하면 받지 않는다. a를 a2로 정하면 받을 곳이 없다.

**최소 승인 집합:**
- Claude에게 read-bridge를 여는 데 필요한 최소 승인은 c1 + a1이다.
- d2와 f2는 그 위에서 권한과 감사를 좁히는 권고다.
- b는 정책 문서의 표기 문제라 받는 것 자체를 막지 않는다(b1이면 변경 없음).

#### 4.14.7 e2: 원장·CLI·로그·알림별 노출 경로 (2026-10-13)

e2는 **저장되는 값**에 관한 결정이다. c(응답 투영)와 별개이고, c를 승인해도 e2는 승인되지 않는다.

**제안하는 형태(구현 아님):**
- factory 자식 실패의 `status`를 `failed:FACTORY_CHILD:<예외 타입>`으로 만든다(`scheduler.py:2471`).
- 메시지 본문은 운영자 전용 진단 기록에만 쓴다. 위치는 결정 항목이다.

| # | 경로 | 지금(e1) | e2 적용 후 | 근거 | 남은 위험 | 필요한 증거 |
|---|---|---|---|---|---|---|
| F0 | 자식 결과 파일(`factory_spool/<run>.result.json`, 상태 루트) | `"<타입>: <메시지>"` 전체. 회수 직후 삭제된다(고아 처리 때도 삭제) | 같음. 자식이 쓰고 부모가 읽는 동안 잠깐 존재한다. 부모가 메시지를 진단 기록으로 옮긴 뒤 지금처럼 지운다 | `scheduler.py:2296-2303`, `:2319-2322`, `:2435-2437`, `:2500-2501` | 부모가 죽은 뒤 남은 결과 파일은 고아 처리에서 **읽지 않고** 지운다(`scheduler.py:2423-2437`). 단 고아 처리는 meta 파일만 기준으로 찾으므로, meta 없이 남은 결과 파일은 지워지지 않을 수 있다(SOURCE_SUPPORTED, 발생 가능성 NOT_VERIFIED). 그 사이 상태 루트에 있으면 백업(F5)에 들어간다 | spool 디렉터리의 잔존 파일 유무(O-9 시점, Thomas). meta 없는 결과 파일을 만드는 시험(e2 구현 시) |
| F1 | scheduler 이벤트 원장 | 메시지 앞부분 포함(160자 이내) | 예외 타입까지만. **이미 기록된 행은 그대로 남는다**: 원장은 추가 전용이고 행마다 무결성 해시가 있어 소급 수정은 제안하지 않는다(`events.py:21-22`). Claude 쪽 과거 행은 c1이 막는다 | `scheduler.py:2389-2390` | 과거 행에 메시지 앞부분이 남는다(소급하지 않음) | e2 적용 뒤 첫 factory 실패 행의 형태(O-9) |
| F2 | 일정 `last_status` → read-bridge `schedules`(60자), 운영자 CLI `scheduler_cli list`(원문), 제거 이벤트의 재기록 | 메시지 포함 | 예외 타입까지만. 다음 실행이 덮어쓸 때까지는 이전 값이 남는다 | `scheduler.py:2391`, `scheduler_cli.py:304-305`, `scheduler.py:886-888` | 다음 실행 전까지 이전 `last_status`가 남는다 | 같은 일정의 다음 실행 뒤 값(O-9) |
| F3 | 운영자 실패 알림(Thomas 등록 채널, Telegram) | `status:` 원문. **호스트 밖(메신저 서비스)으로 나간다** | 기본은 예외 타입까지만. 알림에 메시지를 붙일지는 별도 결정(e-알림) | `scheduler.py:787-815`, `scheduler_cli.py:99-112` | e-알림에서 원문 첨부를 고르면 계속 호스트 밖으로 나간다. 이미 보낸 메시지는 메신저 쪽에 남는다 | 알림 문구 확인(Thomas) |
| F4 | scheduler 서비스 stderr(서비스 로그) | 결과 줄에 원문 | 예외 타입까지만. 진단 기록을 stderr로 쓰지 않는 한 그렇다 | `scheduler_cli.py:469-470` | 진단 기록을 stderr로 쓰면 다시 생긴다. 이미 쓴 로그는 보존 기간 동안 남는다 | 구현 시험에서 stderr에 표지 없음(CL-e2) |
| F5 | core 백업(상태 루트 전체, age 암호화, 매일 호스트 밖 Mac으로) | F0(그 순간 있으면)·F1·F2를 포함한다 | F1·F2의 새 값은 타입까지만. 진단 기록이 상태 루트에 있으면 그것도 백업된다(암호화, Thomas 열쇠) | `harness_backup.sh:7-13,26-29` | 이미 만든 백업에는 원문이 남는다(소급 정리 없음). 진단 기록을 상태 루트에 두면 계속 백업된다 | 백업 보존 기간(keep 7, `harness_backup.sh:7`)이 지나면 과거 사본이 사라진다는 것은 소스 근거이고, 실제 보관 상태는 OPS_REQUIRED |
| F6 | hermes(read-bridge 경유) | 모델은 60자 `reply`만, 컨테이너(shim 프로세스)는 `data` 원본까지 | 새 값은 타입까지만. 과거 행은 원장에 남은 만큼 보인다 | §4.12.5, `read_bridge_mcp.py:74-86,161-173` | 과거 행은 hermes 컨테이너가 계속 받을 수 있다. 모델은 60자 `reply`만 본다(저장소 shim 기준, 설치본 NOT_VERIFIED) | 설치본 shim 대조(Thomas) |

**e2로 결정할 것:**
1. 진단 기록 위치. 권고: 상태 루트 안의 운영자 전용 파일. 런타임 uid가 쓰고, hermes·Claude 인스턴스에는 마운트되지 않으며, 백업에 포함된다.
2. 알림(F3)에 메시지를 붙일지. 권고: 붙이지 않는다. 메시지는 진단 기록에만 둔다.
3. 과거 행. 권고: 소급하지 않는다.
4. spool 잔존(F0). 고아 처리가 meta 기준이라 meta 없는 결과 파일이 남을 수 있다. 권고: e2 구현 시 결과 파일만 남은 경우도 지우는 시험을 둔다. 이것은 결정이 아니라 구현 조건이다.

e2 범위 밖의 실패 경로(`UNEXPECTED`는 이미 타입까지만, 나머지는 고정 코드)는 바꾸지 않는다.

#### 4.14.8 결정별 별도 승인 체크리스트 (2026-10-13)

각 목록은 **독립적으로 승인한다.** 한 목록의 체크가 다른 목록의 체크를 대신하지 않는다. **체크(`[x]`)는 사실(시험 통과 등)에만 쓴다.** 결정 칸은 Thomas의 항목별 명시 확정 전에는 체크하지 않고 상태를 적는다(2026-10-15 정정, §4.18.1). 시험 통과는 승인이 아니다.

**CL-c1 응답 투영**
- [x] Thomas 항목별 명시 확정: c1 채택(c2·c3 아님) — EXPLICITLY_APPROVED 2026-10-16, 저장소 구현 범위만(운영 권한 아님). PR-B #1223 MERGED(`8979e702`)
- [ ] 명시적 구현 승인(C3): `store_reads.py` 투영 함수, `read_bridge.py` 모드, `read_bridge_cli.py` — 구현은 됐다(IMPLEMENTED_PENDING_REVIEW). 근거는 지시의 해석(§4.17.2)이고 명시적 G2가 아니다. 2026-10-15 단일 프로필 구조 변경(R-01)은 명시 지시(§4.18)
- [x] 저장소 시험 통과: X-0, X-1, X-2, X-3, X-4, X-5a, X-5b, X-7, X-8, X-8b, X-9, X-9b, X-10, X-12 (2026-10-13, 2026-10-15 재실행. 시험 통과이며 승인이 아니다)
- [x] X-5b에서 거부 문구에 원장 줄 내용이 없음을 확인(`LEDGER_UNREADABLE`, 2026-10-13)
- [ ] 운영 검증: O-6(스키마), O-8(원문 없음)

**CL-a1 전용 인스턴스**
- [x] Thomas 항목별 명시 확정: a1 채택(코드 부분: uid 허용 목록 필수) — EXPLICITLY_APPROVED 2026-10-16, 저장소 구현 범위만(운영 권한 아님). PR-B #1223 MERGED(`8979e702`)
- [ ] Claude 계정 uid·gid 값 확정(C1·C5)
- [ ] 구현 승인(C3): `docker-compose.yml` 서비스, `docs/DEPLOYMENT.md`
- [ ] 저장소 시험 통과: compose 리터럴 고정, 소켓 경로가 `bridge/` 밖, hermes 마운트와 겹치지 않음, 빈 허용 목록이면 listen 거부, X-5c — **부분: 빈 허용 목록 거부·X-5c만 통과. compose 시험은 compose 미반영이라 없음**
- [ ] 호스트 반영 승인(C5·C6): 소켓 디렉터리, 서비스 기동
- [ ] 운영 검증: O-1, O-2, O-3, O-4
- [ ] **전제 확인:** CL-c1이 끝나기 전에는 Claude를 받지 않음 — 유지 중(CL-c1 운영 검증 미완)

**CL-d2 요청 감사**
- [x] Thomas 항목별 명시 확정: d2 채택, 기록 실패 시 읽기 거부 — EXPLICITLY_APPROVED 2026-10-16, 저장소 구현 범위만(운영 권한 아님). PR-B #1223 MERGED(`8979e702`)
- [ ] 로그 디렉터리 위치 확정(상태 루트 밖, Claude 쓰기 불가)
- [ ] 구현 승인(C3): 기록 훅, compose 로그 마운트 — **부분: 기록 훅만 구현(지시 해석). compose는 로그 위치 미정**
- [x] 저장소 시험 통과: X-6, 기록에 응답 내용 없음 (2026-10-13). 2026-10-15: 인증 거부·자격 확인 불가·디코딩 실패·busy 기록, X-5c 재작성(§4.18.2)
- [ ] 운영 검증: O-7

**CL-f2 읽기 범위**
- [x] Thomas 항목별 명시 확정: f2 채택(지금은 2개 read. `:ro`는 운영 단계라 미포함) — EXPLICITLY_APPROVED 2026-10-16, 저장소 구현 범위만(운영 권한 아님). PR-B #1223 MERGED(`8979e702`)
- [ ] 구현 승인(C3): 인스턴스 부분집합, 플래그, compose `:ro` — **부분: 부분집합·플래그만 구현(지시 해석). compose `:ro` 없음**
- [ ] 저장소 시험 통과: X-11, 읽기 전용 `tmp_path`에서 6개 성공 — **부분: X-11 통과. 2026-10-15부터 프로필은 2개만 서비스(4개 보류, §4.18.3). 읽기 전용 `tmp_path` 시험은 저장소에 없음**
- [ ] 운영 검증: O-5, O-6(실제 데이터 모양 포함)

**CL-e2 원장 정규화 (Option C와 별개)**
- [x] Thomas 항목별 명시 확정: e2 채택(e1·e3 아님) — EXPLICITLY_APPROVED 2026-10-16, 저장소 내부 보안 구현만(운영 아님). PR-C #1224 MERGED(`0b6fd69c`, §4.23, §4.24)
- [x] 하위 결정 항목별 명시 확정: 진단 기록 위치(기본 경로), 알림에 메시지 첨부 안 함, 과거 행 비소급 — e2와 함께 2026-10-16 확정. E-01 E1-a(문서), E-02–E-04(저장소 보안 수정) APPROVED(§4.23)
- [x] 명시적 구현 승인: `scheduler.py`, 진단 기록(E-02–E-04 포함) — 2026-10-16 명시 승인. PR-C #1224 MERGED(`0b6fd69c`), 운영 미배포
- [x] 저장소 시험 통과: 가짜 표지를 넣은 자식 실패 → 원장·`last_status`·알림·서비스 로그에 표지 없음, 진단 기록에만 있음. 시험 45건, PR·main CI 통과(2026-10-16)
- [ ] 운영 검증: O-9

**CL-b3 정책 actor**
- [ ] Thomas 항목별 명시 확정: b3(지금 b1) — PROVISIONALLY_ACCEPTED(2026-10-13 권고 일괄 수용 지시의 해석, 항목별 확정 대기), §4.17
- [ ] 다음 정책 버전에서 b2를 할 때: 조항과 부분집합의 양방향 고정 시험

#### 4.14.9 결정 전 보류 (2026-10-13 확인)

위 체크리스트의 결정 칸이 채워질 때까지 다음을 하지 않는다.
- 런타임 코드·정책·compose·실행기·설정 변경
- 계정·그룹·파일 권한·소켓·마운트·cron 변경
- Docker 명령
- 커밋·push·PR·merge·배포

문서 변경은 커밋하지 않은 worktree(`/root/wt-option-c`)에 남아 있다. 승인 없이 결정 상태를 바꾸지 않는다.

### 4.15 잔여 항목 정리 (2026-10-13)

새로 조사한 것은 D-16의 배너 형식(4.15.4)뿐이다. 나머지는 §4.8.2·§4.8.3·§4.12의 근거를 표준 형식으로 다시 묶었다. 판정과 결정 상태는 바꾸지 않았다.

#### 4.15.1 W-11·W-12: CLI intake와 dispatch door의 차이 (E 유지)

| 차이 | CLI intake | dispatch door | 해소 선택지 | 근거 |
|---|---|---|---|---|
| 요청자 기록 | `thomas`/`real_thomas`/authenticated, 채널 `manual`(기본값) | `assistant_bridge`/`agent`, peer uid 기록 없음 | (i) Claude 전용 행위자 프로필 `claude`(worker의 `_ACTOR_PROFILES`에 추가, door는 전용 인스턴스에서만 그 프로필을 보냄) (ii) 지금처럼 `assistant_bridge`로 묶음(Claude·Hermes 구분 불가) | `intake.py:135-139`, `pipeline_worker.py:155-173` |
| 검증 정책 선택 | 요청마다 `--independent-validation[=auto]`, `--revise` | 서비스 명령에 고정(`auto` + `revise`) | (i) 고정값을 받아들임(지금 CLI 기본값보다 엄격) (ii) 요청 키 추가(정책 변경) | `cli.py:116-129`, `docker-compose.yml:619-632` |
| priority | `--important` → HIGH | 없음(NORMAL) | 받아들임, 또는 키 추가 | `intake.py:148` |
| 모델 tier | `tiered_provider_selector` | 없음 | 받아들임, 또는 worker에 추가(런타임 변경) | `cli.py:212-215` |
| 쓰기 | `--write-output`(Safety-Flag Gate 아래) | 거부(`write_path` 금지) | Claude는 쓰지 않음(권고). 쓰기가 필요하면 Thomas | `dispatch_bridge.py:29-31,103-109` |
| development kind | 받음(planner 5종) | 없음(4종) | 받아들임 | `planner.py:81-90`, `dispatch_bridge.py:97` |
| door 접근 범위 | 분석 실행만 | 같은 소켓으로 v3 workflow와 위임 스케줄 변경까지 | 전용 인스턴스에서 v3를 끄는 모드(런타임 변경) | `dispatch_bridge.py:120-131`, `GOVERNANCE_POLICY.yaml:398-412` |
| 공유 허용 목록 | — | `MVP_BRIDGE_CLIENT_UID`·`_GID`가 네 door에 공유. 넣으면 switch까지 열림 | 공유 변수에는 넣지 않는다. 전용 인스턴스의 리터럴만 | `docker-compose.yml:467,488,542,547,727,732,794,798` |

**결정 필요(D-14):**
- (a) Claude가 intake를 돌리지 않음 — 권고, D
- (b) 전용 dispatch 인스턴스: v2만, 행위자 프로필 `claude`, 리터럴 uid·gid — C. 위 표의 (i)들을 함께 정한다.
- (c) 공유 door 그대로 — 비권고

#### 4.15.2 W-13: 기록 기반 판단과 프로세스 상태 판단 (E 유지)

| 판단 | 필요한 값 | 어디서 | Claude에게 |
|---|---|---|---|
| "자율 진입이 다음 cycle에 열릴 수 있나, 무엇이 막나" | `live_entry_possible`, `blocking`, `unknown` | read-bridge(기록 기반, 한 주기 지연, 오래되면 None) | B(D-11 후). 운영 보고와 이상 감지에 충분 |
| "scheduler 프로세스가 지금 주문을 낼 수 있는 상태인가"(스크립트 전제조건) | `ready`, 종료 코드 | scheduler 프로세스 안에서만 | D. 돈 경로 쪽 전제조건이다 |
| "실행 단계가 무엇인가" | `execution_stage` | 공유 resolver(env 무관) | B |

Thomas의 판단이 필요한 이유는 두 가지다.
- 기록 기반 답은 한 주기 늦고, 오래되면 None이다. 이것을 Claude 보고에 써도 되는지는 운영상의 선택이다.
- 프로세스 `ready`를 Claude가 아예 보지 않는 것을 받아들일지도 정해야 한다.

#### 4.15.3 W-30: D(선행 변경) 유지

§4.12.3–§4.12.4와 같다.
- C5 점검 항목: `harness_backup.sh`에 Claude 홈용 두 번째 루트 변수를 추가한다. 저장소 사본과 `/root/backups/` 설치본 둘 다다.
- C6 중단 조건: 분리 후 첫 core 백업 로그가 `claude-config=5/5`가 아니거나, transcripts에 새 대화가 없으면 멈춘다.
- **NOT_VERIFIED:** 어느 계정의 crontab에서 도는지, 설치본의 실제 내용.

**C5용 변경 명세 (문서만, 스크립트는 바꾸지 않았다):**

| 위치 | 지금 | 바꿀 것 |
|---|---|---|
| 루트 변수 | `HOST_ROOT="${HARNESS_BACKUP_HOST_ROOT:-/root}"` 하나(`harness_backup.sh:57`) | 두 번째 변수 `CLAUDE_ROOT="${HARNESS_BACKUP_CLAUDE_ROOT:-$HOST_ROOT}"`. 기본값이 지금과 같으므로 분리 전 동작은 그대로다 |
| transcripts 선택·검사·아카이브 | `.claude/projects`, `.claude/prompt-collector`를 `HOST_ROOT` 기준으로 찾는다(`:94-105`, `:114-115`, `:125`, `:143-156`) | `CLAUDE_ROOT` 기준. 아카이브의 멤버 경로(`.claude/…`)는 그대로 둔다. 그래야 `restore_transcripts.sh`(대상 디렉터리 인자, `:42`)와 맞는다 |
| core의 Claude 설정 멤버 | `.claude/settings.json`, `.claude/settings.local.json`, `.claude/CLAUDE.md`, `.claude/skills`를 `HOST_ROOT` 기준으로 찾는다(`:246-251`, `:266-278`) | `CLAUDE_ROOT` 기준. tar의 `-C`가 두 루트를 쓰게 한다. `$THOMAS/.claude/settings.local.json`(저장소 쪽)은 `HOST_ROOT`에 남는다 |
| 자격 파일 검사 | `.credentials.json`·`.claude.json`이 아카이브에 들어가면 실패(`:293-295`) | 그대로. 두 루트 모두에 적용됨을 시험으로 고정 |
| 설치본 | `/root/backups/backup-governance-state.sh` | 저장소 사본과 같은 내용으로 다시 설치(Thomas). 설치본의 현재 내용은 NOT_VERIFIED |

**시험 명세 (합성 디렉터리):**
- 두 루트를 가진 합성 디렉터리로 core와 transcripts를 만든다. 그 결과로 다음을 확인한다.
  - (i) `CLAUDE_ROOT`의 설정 5개가 `claude-config=5/5`로 들어감
  - (ii) `HOST_ROOT`에만 있는 옛 `.claude/projects`가 transcripts에 들어가지 않음
  - (iii) 두 루트 어디에 `.credentials.json`이 있어도 실패
  - (iv) 변수를 주지 않으면 지금과 같은 파일 목록
- 이 스크립트에 대한 기존 셸 시험 위치는 이번에 확인하지 않았다(NOT_VERIFIED).

#### 4.15.4 D-16: 실행기 고정 패턴 명세 (대안 1, 구현 아님)

**배너 형식 (VERIFIED):**
- door·worker: `<LABEL>: listening on <path> (<banner>)`(`cli_common.py:170`)
  - door 배너: `concurrency=<n>+<m> queued, uids=<[…]|unrestricted (group only)>`(`socket_door.py:701-714`)
  - worker 배너: `kinds=[…], actor=…, validation=<always|auto|automatic-only>, revise=<on|off>, …`(`pipeline_worker_cli.py:127-133`)
  - label: `READ_BRIDGE`, `DISPATCH_BRIDGE`, `KNOWLEDGE_BRIDGE`, `SWITCH_BRIDGE`, `PIPELINE_WORKER`(각 `*_cli.py`)
- frontdesk: 켜졌을 때만 `FRONTDESK: conversational mode ON (model: …; …)`(`operator_cli.py:150-153`). 꺼졌을 때는 줄이 없다.

**닫힌 목록 (실행기 코드의 상수):**

| # | 컨테이너 | 앵커 정규식(줄 시작) | 돌려줄 토큰 | 기대값 |
|---|---|---|---|---|
| P1 | `thomas-read-bridge` | `^READ_BRIDGE: listening on \S+ \(.*\buids=(\[[0-9, ]{1,40}\]\|unrestricted \(group only\))\)?` | `uids` 값 | `[10000]`(hermes용) |
| P2 | `thomas-dispatch-bridge` | 같은 형식, `DISPATCH_BRIDGE` | `uids` | `[10000]` |
| P3 | `thomas-knowledge-bridge` | 같은 형식, `KNOWLEDGE_BRIDGE` | `uids` | `[10000]` |
| P4 | `thomas-switch-bridge` | 같은 형식, `SWITCH_BRIDGE` | `uids` | `[10000]` |
| P5 | `thomas-pipeline-worker` | `^PIPELINE_WORKER: listening on \S+ \(.*\bvalidation=(always\|auto\|automatic-only), revise=(on\|off)\b` | `validation`, `revise` | `auto`, `on` |
| P6 | `thomas-pipeline-worker` | P5의 줄에서 `uids=` | `uids` | `[10001]` |
| P7 | `thomas-operator` | `^FRONTDESK: conversational mode ON \(` | `ON` 또는 없음 | 운영 설정에 따름 |
| P8 | Claude 전용 read-bridge(D-11 a1 이후) | P1 형식 | `uids` | `[<claude uid>]` |

- 반환 필드는 `{check, container, token, verdict}`뿐이다. `token`은 위 캡처 그룹의 값이고 최대 64자다.
- 경로(`listening on <path>`), 모델 id, 그 밖의 줄 내용은 돌려주지 않는다.

**판정 규칙 (fail-closed):**

| 상황 | verdict | 비고 |
|---|---|---|
| 기대값과 일치 | PASS | — |
| 일치했지만 값이 다름(예: `unrestricted (group only)`, `validation=automatic-only`) | STOP | 경계 설정이 다르다 |
| 컨테이너 없음 | STOP | — |
| 로그 읽기 실패, 시간 초과 | STOP | 재시도하지 않는다 |
| 일치 없음 | UNKNOWN | 보존 기간 초과(10m×3, 재생성 시 소실)일 수 있다. 사후 확인 전체를 통과로 보지 않는다 |
| 같은 컨테이너에서 서로 다른 값이 여러 번 일치 | 가장 마지막 줄을 쓴다. 마지막 기동인지 판별할 수 없으면 UNKNOWN | — |
| P7 일치 없음 | `NOT_OBSERVED` | 꺼짐인지 로그 소실인지 구별할 수 없다. "꺼짐"이라고 단정하지 않는다 |

**읽는 범위:** 컨테이너당 마지막 N줄(권고 N=2000, 실행기 상수). Claude는 N, 컨테이너, 패턴을 지정할 수 없다. 동작 인자가 없다.

**감사:** 실행기 감사(§4.5)에 `requested_by_uid`, 동작 이름, 시각, 각 check의 `verdict`와 `token`을 남긴다. 원문 줄, 주변 줄, traceback은 응답에도 감사에도 남기지 않는다.

**시험 명세 (C3, 합성 로그 텍스트):**
- 각 P에 대해 정상 줄 / 다른 값 / 없음 / 여러 줄 / 경로·모델 id에 가짜 표지가 든 줄을 넣는다. 응답과 감사에 경로·모델 id·표지가 없어야 한다.
- 정규식은 줄 시작 앵커가 없으면 일치하지 않는다(다른 서비스가 같은 문자열을 출력에 섞는 경우).

**정규식 대조 (저장소 밖, 2026-10-13):**
- 소스의 `socket_door.describe_admission`으로 배너를 합성하고 경로에 가짜 표지를 넣어 P1–P7 형식을 확인했다.
- 결과는 모두 일치했다: uid 토큰, `unrestricted (group only)`, worker 두 값, frontdesk.
- 줄 앞에 다른 문자가 있으면 일치하지 않았다.
- 토큰에 경로 표지가 섞이지 않았다.
- 실제 컨테이너 로그는 보지 않았다.

**의존성:**
- 실행기 존재: D-1, D-3
- 실행기 계정: D-10
- 읽기 동작의 승인 필요 여부: D-4
- P8: D-11 a

모두 미결정이다. 그 전에 할 수 있는 것은 이 명세까지다.

### 4.16 승인 게이트 통합표 (Approval Gate Matrix, 2026-10-13)

이 표는 흩어진 결정, 시험, 운영 검증을 한 줄씩 연결한다. 근거와 선택지의 상세는 표의 "→" 절에 있다. 이 표에 새 상태 값은 없다.

**표기:**
- **승인 상태:** `미결정`, 그리고 2026-10-15부터 §4.18.1의 상태 용어(RECOMMENDED / PROVISIONALLY_ACCEPTED / IMPLEMENTED_PENDING_REVIEW / EXPLICITLY_APPROVED / OPERATIONALLY_AUTHORIZED). 결정이 내려지면 그때 Thomas의 결정 문구와 날짜를 그대로 적는다.
- **근거 등급:** §4.14.4와 같다(VERIFIED / SOURCE_SUPPORTED / SCRATCH / INFERRED / OPS_REQUIRED / NOT_VERIFIED).
- **시험 상태:** §4.14.5의 단계(① 명세만, ② 저장소 밖 합성, ③ 저장소 구현 후 실행, ④ 운영)와 §4.6의 결과 표기(PASS / FAIL / NOT_TESTED / BLOCKED).
- **요청 표기와의 대응:** SPEC_ONLY = ①, SCRATCHPAD_TESTED = ② 통과, REPO_TESTED = ③ PASS(**현재 0건**), OPERATIONAL_TESTED = ④ PASS(**현재 0건**), BLOCKED = 선행 결정이나 구현이 없어 실행할 수 없음.
- **진행 순서(G1–G7):**

  | 단계 | 내용 |
  |---|---|
  | G1 | 결정 기록 |
  | G2 | 명시적 구현 승인 |
  | G3 | 승인된 범위의 구현 |
  | G4 | 필수 저장소 시험 |
  | G5 | 운영 검증 승인과 수행 |
  | G6 | 결과 검토 |
  | G7 | 다음 단계의 별도 승인 |

  건너뛰는 단계는 없다. 권고가 적혀 있다는 것은 G1도 아니다.

**Claude read-bridge 접근 게이트:** **BLOCKED**. 다음이 모두 끝날 때까지 막혀 있다.
- c의 G1–G6
- a의 G1–G6
- 골랐다면 d·f의 G1–G6

2026-10-15 현재 c·a·d·f는 G1이 PROVISIONALLY_ACCEPTED(권고 일괄 수용의 해석, 항목별 명시 확정 대기)이고, 코드 부분의 G3·G4는 IMPLEMENTED_PENDING_REVIEW다. compose·계정·G5–G6이 없어 접근 게이트는 BLOCKED 그대로다(§4.17, §4.18).

#### 4.16.1 결정별 통합표

| ID | 결정 대상 | 선택지 | 권고(승인 아님) | 근거 등급 | 의존성·차단 조건 | 접근·보안 영향 | 구현 범위 | 필수 저장소 시험(실행) | 운영 검증 | 승인 상태 | 다음 허용 작업 | 차단 사유 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D-11 c | Claude에게 나가는 `scheduler_events`·`schedules` 응답의 필드 투영 → §4.13, §4.14 c | c1 / c2 / c3 | c1 | 노출 경로 VERIFIED, 계약 SCRATCH, hermes shim SOURCE_SUPPORTED(설치본 NOT_VERIFIED) | 선행 없음. a의 전제 | **응답만** 제한한다. 원장·spool·로그·알림·백업의 원문은 정리하지 않는다(그것은 e) | `store_reads.py`, `read_bridge.py`, `read_bridge_cli.py` | X-0–X-4, X-5a, X-5b, X-7–X-9b, X-10, X-12: ③ PASS(2026-10-13, §4.17.4) | O-6, O-8. NOT_TESTED | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-B `8979e702`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: c1(2026-10-13 권고 일괄 수용, 항목별 확정 대기). G2는 지시 해석, G3·G4 완료 = IMPLEMENTED_PENDING_REVIEW) | G5 운영 검증 승인(Claude 인스턴스·계정이 있어야 함) | 운영 검증 없음, a1 compose·계정 없음 → 접근 BLOCKED |
| D-11 a | Claude의 read-bridge 접근 방식 → §4.12.1, §4.14 a | a1 / a2 / a3 / a4 | a1 | 공유 변수 VERIFIED. 계정 값·소켓 권한 NOT_VERIFIED | c(받기 전 필수). Claude 계정 uid·gid(C1·C5) | 닿는 곳이 소켓 하나로 한정된다. 세부 통제는 §4.16.3 | `docker-compose.yml`(서비스 1), `docs/DEPLOYMENT.md`, 호스트 소켓 디렉터리(운영) | 허용 목록 필수 시험·X-5c ③ PASS. compose 고정 시험은 ①(compose 미반영) | O-1–O-4. NOT_TESTED | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-B `8979e702`(코드 부분)**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: a1(2026-10-13 권고 일괄 수용, 항목별 확정 대기). 코드 부분만 G3·G4 완료 = IMPLEMENTED_PENDING_REVIEW) | uid·gid·소켓 경로 결정(§4.17.5 #2·#3) → compose 구현 승인 | 계정 값·소켓 경로 미정 |
| D-11 b | 정책상 Claude 읽기를 어느 조항에 둘지 → §4.14 b | b1 / b2 / b3 | b3 | VERIFIED | f(b2일 때 조항 = 부분집합) | 정책 표기 문제. 응답·범위는 c·f가 정한다. 접근을 막지 않는다 | b1 없음. b2는 정책 파일, `read_bridge.py` | b2: 조항·부분집합 양방향 시험(①) | — | PROVISIONALLY_ACCEPTED: b3(2026-10-13 권고 일괄 수용, 항목별 확정 대기)(지금은 b1: 변경 없음) | 다음 정책 버전에서 b2 구현 승인 | 지금은 없음 |
| D-11 d | Claude 읽기의 요청 감사 → §4.14 d | d1 / d2 / d3 | d2(기록 실패 시 거부) | 기록 없음 VERIFIED, 로그 보존 SOURCE_SUPPORTED(실제 값 OPS_REQUIRED) | a1. 위치는 f 고려(상태 루트 밖) | 사후에 Claude 읽기를 구분한다. 위치·필드·보존·실패 동작은 §4.16.3 | 기록 훅(`socket_door.py` 또는 `read_bridge.py`), compose 로그 디렉터리. **위치 미확정** | X-6, 기록 내용 시험 ③ PASS | O-7. NOT_TESTED | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-B `8979e702`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: d2(2026-10-13 권고 일괄 수용, 항목별 확정 대기). 코드 G3·G4 완료 = IMPLEMENTED_PENDING_REVIEW) | 로그 위치 결정(§4.17.5 #4) → compose 구현 승인 | 로그 위치 미정 |
| D-11 f | Claude 인스턴스의 읽기 범위와 마운트 → §4.14 f | f1 / f2 / f3 | f2 | 잠금 VERIFIED, 6개 쓰기 0건 SOURCE_SUPPORTED + SCRATCH, 실제 `:ro` OPS_REQUIRED | a1, b | 읽기 15개 → 6개, 상태 루트 읽기 전용. 축소·잠금·`:ro`는 §4.16.3에서 별도 통제. **f2의 안전성은 운영 검증 전 확정 사실이 아니다** | `read_bridge.py`, `read_bridge_cli.py`, compose `:ro` | X-11 ③ PASS. 읽기 전용 `tmp_path` 시험은 저장소에 없음(scratch 쓰기 감시만) | O-5, O-6. NOT_TESTED | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-B `8979e702`(2개 read)**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: f2(2026-10-13 권고 일괄 수용, 항목별 확정 대기). 부분집합 코드 G3·G4 완료(2026-10-15: 투영된 2개만, 4개 보류 §4.18.3) = IMPLEMENTED_PENDING_REVIEW) | compose `:ro` 구현 승인 → O-5·O-6 | compose 미반영, 실제 `:ro` 미검증 |
| D-11 e | 원장 등에 **저장되는** factory 실패 문구 정규화 → §4.14 e, §4.14.7 | e1 / e2 / e3 | e2(Option C와 분리) | F1–F4 VERIFIED, 메시지 내용 INFERRED, 키·env 포함 여부 NOT_VERIFIED | 독립. c의 승인으로 함께 승인되지 않는다 | 원장·`last_status`·CLI·stderr·hermes의 **새 값**에서 메시지가 빠진다. 과거 행·백업은 그대로 | `scheduler.py`(`:2296`, `:2471`), 진단 기록 | factory 자식 시험 3개(8건) ③ PASS | O-9. NOT_TESTED | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-C `0b6fd69c`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: e2(2026-10-13 권고 일괄 수용, 항목별 확정 대기). G3·G4 완료 = IMPLEMENTED_PENDING_REVIEW) | 배포 승인(별도) → 배포 뒤 O-9 | 커밋·배포 없음 |
| D-11 e-1 | 진단 메시지의 저장 위치와 접근 권한 | 상태 루트 안 운영자 전용 / 그 밖 | 상태 루트 안, hermes·Claude 인스턴스 미마운트 | DESIGN_ASSUMPTION | e2를 고를 때만 | 위치에 따라 백업(F5) 포함 여부가 정해진다 | 미확정 | 진단 파일에만 표지 ③ PASS | O-9 | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-C `0b6fd69c`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED(2026-10-13 권고 일괄 수용, 항목별 확정 대기). 기본 경로로 구현(경로 확인은 §4.17.5 #5)) | 경로 확인 | 없음 |
| D-11 e-2 | 운영자 알림(F3)에 원문을 붙일지 | 붙임 / 안 붙임 | 안 붙임 | VERIFIED(지금은 원문을 붙임) | e2 | 붙이면 원문이 계속 호스트 밖으로 나간다 | `scheduler.py:787-815` | 알림에 표지 없음 ③ PASS | 알림 문구(Thomas) | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-C `0b6fd69c`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: 안 붙임(2026-10-13 권고 일괄 수용, 항목별 확정 대기). 구현됨(알림은 status를 쓴다)) | O-9 때 알림 문구 확인 | 배포 없음 |
| D-11 e-3 | 과거 원장·백업을 소급 정리하지 않는 데 동의할지 | 비소급 / 소급 | 비소급 | 원장 추가 전용 + 행 해시 VERIFIED(`events.py:21-22`) | e2 | 과거 행·기존 백업에 원문이 남는다. Claude 쪽은 c1이 막는다 | 없음(비소급) | — | — | **EXPLICITLY_APPROVED 2026-10-16(저장소 구현 범위)** → **MERGED PR-C `0b6fd69c`**, 운영 미배포. (이전 기록: PROVISIONALLY_ACCEPTED: 비소급(2026-10-13 권고 일괄 수용, 항목별 확정 대기)) | 없음 | 없음 |
| D-11 e-4 | spool 잔존(F0) | — (결정 아님, 구현 조건) | meta 없는 결과 파일도 지우는 시험 추가 | 고아 처리가 meta 기준 SOURCE_SUPPORTED, 발생 가능성 NOT_VERIFIED | e2 구현 | 잔존하면 상태 루트·백업에 남는다 | `scheduler.py:2399-2437` | meta 없는 결과·`.tmp` 삭제 시험 ③ PASS | spool 잔존 확인(Thomas) | **MERGED PR-C `0b6fd69c`**(e2와 함께), 운영 미배포. (이전 기록: 구현 조건 충족(G3·G4)) | O-9 때 spool 확인 | 배포 없음 |
| D-1 | 대안 선택(실행기를 둘지) → §5 | A / B / C / D | C | SOURCE_SUPPORTED | — | 배포·Rollback을 Claude 밖 실행기로 옮긴다. **D-11 접근 승인과 별개** | 미확정(C3) | N-7–N-12(①) | N-1–N-6(C6, ④) | PROVISIONALLY_ACCEPTED: 대안 C(2026-10-13 권고 일괄 수용, 항목별 확정 대기) | D-2·D-4·D-10 결정 → 실행기 구현 승인(§4.17.5 #17) | D-2·D-4·D-10 미결정 |
| D-3 | 실행기·배포 동작 유형·응답 스키마의 Reuse-first 예외 | 승인 / 거절 / 범위 한정 | §4.3 범위로 한정 | — | D-1 | 새 구성요소를 허용한다 | 미확정 | — | — | PROVISIONALLY_ACCEPTED: §4.3 범위로 한정(2026-10-13 권고 일괄 수용, 항목별 확정 대기) | D-1과 같음 | D-2·D-4·D-10 미결정 |
| D-2 | 승인 채널 → §4.4 | R1 / R2 / R3 | R2 | 서명 위조 저항은 DESIGN_ASSUMPTION(구현 없음). 우회는 root에 대해 막지 못함(§4.10) | D-1 | 실행기의 promote·rollback 승인 | 미확정 | N-8–N-11(①) | — | 미결정 | G1 | D-1 미결정 |
| D-4 | `inspect_candidate`를 승인 없이 허용할지 | 허용 / 불허 | 허용(env·Mounts 제외) | DESIGN_ASSUMPTION | D-1 | 읽기 동작의 승인 필요 여부 | 미확정 | — | — | 미결정 | G1 | D-1 미결정 |
| D-10 | 실행기 계정 → §4.2 | E1 / E2 / E3 | E1(root) | `/root`·상태 루트 0700 VERIFIED(호스트 메타데이터) | D-1 | 실행기가 `.env`·상태에 닿는 방식 | 미확정 | — | N-4, N-5 | 미결정 | G1 | D-1 미결정 |
| D-16 | 기동 배너 확인 방식 → §4.12.2, §4.15.4 | 대안 1 / 대안 2 | 대안 1(P1–P8) | 배너 형식 VERIFIED, 정규식 SCRATCH | D-1, D-3, D-4, D-10. P8은 D-11 a | 실행기가 로그를 읽고 토큰만 돌려준다. 원문 비노출, fail-closed, 감사 | 미확정(실행기 코드) | P별 합성 로그 시험(①) | O-4(P8) | 미결정 | G1 | 실행기 미승인 |
| D-14 W-11·W-12 | CLI intake를 Claude가 어떻게 처리할지 → §4.15.1 | (a) 돌리지 않음 / (b) 전용 v2 인스턴스 / (c) 공유 door | (a) | SOURCE_SUPPORTED | (b)이면 D-11 a와 같은 공유 변수 위험 | (c)는 스케줄 위임·switch까지 연다 | (b)이면 worker 프로필, 전용 인스턴스 | 프로필 기록·v3 거부 시험(①) | — | 미결정(분류 E) | G1 | 결정 없음 |
| D-14 W-13 | `live_readiness`를 Claude가 어떻게 볼지 → §4.15.2 | B+D 분리 / 둘 다 Thomas | 진입 가능 여부 B, 프로세스 `ready` D | SOURCE_SUPPORTED. 기존 readiness 테스트 279 passed는 기존 동작 | D-11(B의 전제) | 기록 기반, 한 주기 지연 | 없음 | — | — | 미결정(분류 E) | G1 | 결정 없음 |
| W-30 | 백업 스크립트의 Claude 홈 루트 분리 → §4.15.3 | 변경 / 보류 | `CLAUDE_ROOT` 추가 | SOURCE_SUPPORTED. cron 계정·설치본 NOT_VERIFIED | C5 승인 | 분리 뒤 백업이 조용히 빠지는 위험 | `scripts/ops/harness_backup.sh`, `/root/backups/` 설치본 | 합성 디렉터리 시험 4개(①) | C6 첫 백업 로그 `claude-config=5/5` | 분류 D(선행 변경). C5 승인 대기 | C5 계획 검토 | C5 미승인 |

이 표 밖의 결정(D-5–D-9, D-12, D-13, D-15)도 모두 `미결정`이다(§8).

#### 4.16.2 저장소 시험 연결 (X)

| ID | 단계·결과 | 지원하는 결정 |
|---|---|---|
| X-0 | ② 통과, ③ PASS(2026-10-13) | c |
| X-1 | ② 통과(결함 주입 시 실패), ③ PASS(2026-10-13) | c |
| X-2 | ② 통과, ③ PASS(2026-10-13) | c |
| X-3 | ② 통과, ③ PASS(2026-10-13) | c |
| X-4 | ② 통과, ③ PASS(2026-10-13) | c |
| X-5a | ③ PASS(2026-10-13) | c |
| X-5b | ③ PASS(2026-10-13). 거부 문구에 줄 내용 없음 확인(`LEDGER_UNREADABLE`) | c |
| X-5c | ③ PASS(2026-10-13, 실제 소켓) | a |
| X-6 | ③ PASS(2026-10-13, d2 거부 선택) | d |
| X-7 | ② 통과, ③ PASS(2026-10-13) | c |
| X-8 | ② 통과, ③ PASS(2026-10-13) | c |
| X-8b | ② 통과, ③ PASS(2026-10-13) | c(e와 무관: 응답에서 막는 시험) |
| X-9 | ② 통과, ③ PASS(2026-10-13) | c |
| X-9b | ② 통과, ③ PASS(2026-10-13) | c |
| X-10 | ③ PASS(2026-10-13, 기존 read-bridge 시험 그대로) | c(c1/c2에 따라 내용이 다름) |
| X-11 | ③ PASS(2026-10-13) | f |
| X-12 | ③ PASS(2026-10-13) | c |

- ② 통과는 저장소 밖 참조 구현 기준이다. 저장소 시험 통과가 아니다.
- 기존 회귀(671·74·279 passed)는 기존 동작의 확인이다.

#### 4.16.3 운영 검증 연결 (O)과 세부 통제

| ID | 결정 | 상태 | 증거 |
|---|---|---|---|
| O-1 | a | ④ NOT_TESTED | 없음 |
| O-2 | a | ④ NOT_TESTED | 없음 |
| O-3 | a | ④ NOT_TESTED | 없음 |
| O-4 | a, D-16(P8) | ④ NOT_TESTED | 없음 |
| O-5 | f | ④ NOT_TESTED | 없음 |
| O-6 | c, f | ④ NOT_TESTED | 없음(합성 쓰기 감시는 운영 증거가 아님) |
| O-7 | d | ④ NOT_TESTED | 없음 |
| O-8 | c | ④ NOT_TESTED | 없음 |
| O-9 | e, e-1, e-4 | ④ NOT_TESTED | 없음 |

**a·d·f의 세부 통제:** 결정 하나에 묶이지 않도록 따로 추적한다.

| 통제 | 소속 | 상태 | 확인 방법 |
|---|---|---|---|
| UID·GID 값 | a(C1·C5) | NOT_VERIFIED(계정 없음) | 계정 생성 시 기록 |
| 소켓 경로·디렉터리 권한 | a | NOT_VERIFIED | O-1 |
| 허용 목록 리터럴(공유 변수 미사용) | a | ① 명세 | compose 고정 시험 |
| 정책 actor | b | PROVISIONALLY_ACCEPTED(b3, 지금 b1 그대로) | D-11 b |
| 응답 투영 | c | PROVISIONALLY_ACCEPTED, IMPLEMENTED_PENDING_REVIEW | CL-c1 |
| 감사 기록 위치 | d | 미확정 | d 결정 시 |
| 감사 기록 필드(uid, 읽기 이름, 시각, 결과 코드. 내용 제외) | d | ① 명세 | 기록 내용 시험 |
| 감사 보존 범위 | d | 미확정 | d 결정 시 |
| 기록 실패 시 동작 | d | PROVISIONALLY_ACCEPTED(거부). 구현·X-6 통과는 운영 검증이 아니다 | X-6 |
| 읽기 목록 축소(15 → 6, 구현은 15 → 2이고 4개 보류 §4.18.3) | f | ③ X-11 PASS(IMPLEMENTED_PENDING_REVIEW) | X-11 |
| 잠금 파일 처리(`tasks`·`history` 제외로 해소) | f | VERIFIED(잠금 경로), 해소는 ① | X-11 |
| 상태 루트 `:ro` | f | OPS_REQUIRED | O-5, O-6 |

### 4.17 권고 일괄 수용 기록 (2026-10-13; 2026-10-15 정정)

**원문 (2026-10-13T04:02Z):** "권고대로 진행해주고 3시간정도 자리를 비워야되서 결정이 필요한 것들을 수집해놓고 순서대로 작업을 진행해줘"

2026-10-13 권고 일괄 수용 지시를 근거로 구현 방향을 해석했으며, 항목별 명시적 확정은 대기 중이다. 대상은 이 지시 직전 보고의 "Thomas의 다음 결정" 목록에 있던 항목이고, 상태는 PROVISIONALLY_ACCEPTED다(§4.18.1). 그 목록에 없던 결정은 권고가 있더라도 이 상태로도 보지 않고 대기 목록(§4.17.3)에 모은다. **2026-10-15 정정:** 처음 기록은 이 항목들을 "결정(G1)"으로 적었다. 한 문장의 일괄 수용을 항목별 명시 승인으로 표시한 것이어서 고쳤다(보안 검토 S-1·S-2).

#### 4.17.1 구현 방향으로 해석한 항목 (PROVISIONALLY_ACCEPTED, 항목별 확정 대기)

| ID | 해석한 방향 | 남은 하위 값 |
|---|---|---|
| D-11 c | **c1** — Claude 인스턴스에만 §4.13 응답 투영 | 없음 |
| D-11 a | **a1** — Claude 전용 read-bridge 인스턴스 | Claude 계정 uid·gid 값(대기) |
| D-11 b | **b3** — 지금은 기존 `assistant_read` 조항(b1), 다음 정책 버전에서 b2 | 없음 |
| D-11 d | **d2** — 상태 루트 밖 JSONL, 기록 실패 시 읽기 거부 | 로그 디렉터리 경로(대기) |
| D-11 f | **f2** — Claude 인스턴스 읽기 6개 + 상태 루트 `:ro` | 2026-10-15: 구현은 투영된 2개만, 4개 보류(§4.18.3). `:ro`는 운영 단계 |
| D-11 e | **e2** — factory 자식 실패의 원장 status를 예외 타입까지로 | — |
| D-11 e-1 | 진단 메시지는 상태 루트 안 운영자 전용 파일 | 파일 경로(대기, 구현은 기본값 사용) |
| D-11 e-2 | 운영자 알림에 원문을 붙이지 않음 | 없음 |
| D-11 e-3 | 과거 원장·백업은 소급하지 않음 | 없음 |
| D-1 | **대안 C** — 최소 전용 실행기 | 실행기 구현은 D-2·D-4·D-10이 정해진 뒤 |
| D-3 | **§4.3 범위로 한정한 Reuse-first 예외** | 같음 |

**e-1 정정:** 권고 문구의 "hermes·Claude 인스턴스 미마운트"는 f2와 맞지 않는다. f2의 Claude 인스턴스는 상태 루트 전체를 `:ro`로 마운트하기 때문이다. 실제 보장은 "**어떤 read 동작도 진단 파일을 노출하지 않는다**"이다. hermes는 `bridge/`만 마운트하므로 미마운트가 맞다.

#### 4.17.2 G2: 지시의 해석

- **"순서대로 작업을 진행해줘"를 저장소 안 구현(C3 범위 일부)의 G2로 해석했다.** 별도의 명시적 구현 승인을 받은 것이 아니다. Thomas가 돌아와 거부하면 되돌린다.
- **범위:** 커밋하지 않은 worktree `/root/wt-option-c`. 새 동작은 모두 플래그 뒤에 두고 기본값은 꺼짐이다. 그래서 지금의 hermes 인스턴스와 scheduler 동작은 플래그 없이는 바뀌지 않는다. e2만 예외로, 플래그가 아니라 scheduler 기록 형식 자체를 바꾼다.
- **순서:** c1 → f2 → a1의 코드 부분(허용 목록 필수 플래그) → d2 → e2(e-4 포함).
- **하지 않는 것:**
  - 실행기 구현
  - a1의 compose 서비스(uid·gid 미정)
  - 호스트 계정·디렉터리·마운트(C5·C6)
  - Docker
  - `schemas/`에 새 파일 추가
  - 커밋·PR
- **되돌리기:** 이 결정 기록 뒤 바뀐 코드는 `runtime/`·`tests/` 아래 파일뿐이다. 목록은 §4.17.4에 적는다. 되돌리려면 그 목록에 대해 `git -C /root/wt-option-c restore -- <파일>`을 실행하고, 새로 만든 파일은 지운다.

#### 4.17.3 대기 결정 (Thomas가 돌아와 정할 것)

§4.17.5에 권고 기본값과 함께 적는다.

#### 4.17.4 구현 기록 (2026-10-13, 커밋 없음)

**2026-10-15 변경:** 아래 표의 CLI 플래그 4개(`--reads`, `--projection`, `--request-log`, `--require-uid-allowlist`)와 `resolve_allowed_reads`·`READS_SUBSET_INVALID`는 §4.18.2의 단일 프로필(`--profile claude` + `--request-log`)로 대체됐다. 아래 표는 2026-10-13 기록으로 남긴다.

| 결정 | 바뀐 파일 | 내용 | 플래그·기본값 |
|---|---|---|---|
| c1 | `runtime/mvp_runtime/store_reads.py` | §4.13 계약: `project_scheduler_event`, `project_last_status`, 코드 안 JSON Schema로 응답 직전 검증(`REPLY_SCHEMA_INVALID`), `read_schedules`·`read_scheduler_events`의 `projection` 인자 | `projection=None`이면 이전과 같음 |
| c1·f2 | `runtime/mvp_runtime/read_bridge.py` | `apply_read(projection, allowed_reads)`. 부분집합 밖 읽기는 `VERB_NOT_PERMITTED`. `_READS`는 그대로 | 둘 다 기본 꺼짐 |
| a1(코드 부분) | 같은 파일 | `open_door(require_uid_allowlist=True)`이면 허용 목록이 빈 경우 listen 거부(`READ_UID_ALLOWLIST_REQUIRED`) | 기본 꺼짐 |
| d2 | 같은 파일 | `open_door(request_log=…)`: 요청마다 `{at, peer_uid, read, outcome}` 한 줄. 기록 실패 시 응답 대신 `REQUEST_LOG_UNAVAILABLE`. peer uid는 `authorize_peer`가 같은 handler 스레드에서 남긴 값(`_PeerRecordingDoor`) | 기본 꺼짐 |
| c1·f2·a1·d2 | `runtime/mvp_runtime/read_bridge_cli.py` | `--reads`, `--projection v1`, `--request-log`, `--require-uid-allowlist`. 하나라도 주면 배너에 `projection=`, `request_log=`가 붙는다 | 플래그가 없으면 배너도 이전과 같다(D-16 P1) |
| e2, e-1–e-4 | `runtime/mvp_runtime/scheduler.py` | factory 자식 실패 status를 `failed:FACTORY_CHILD:<예외 타입>`으로(`factory_child_failure_status`). 메시지는 `.runtime_governance_state/diagnostics/factory_child_errors.jsonl`에만(최대 2000자, best-effort). 알림·stderr·원장·`last_status`는 status를 쓰므로 함께 타입까지만. 고아 처리에서 meta 없는 `*.result.json`·`*.result.json.tmp`를 읽지 않고 삭제 | 플래그 없음(기록 형식 자체가 바뀐다) |
| 생성 문서 | `docs/DIAGNOSTIC_CODE_INDEX.md` | 새 reason code(`PROJECTION_UNKNOWN`, `REPLY_SCHEMA_INVALID`, `READS_SUBSET_INVALID`, `READ_UID_ALLOWLIST_REQUIRED`, `REQUEST_LOG_UNAVAILABLE`)로 생성 스크립트가 다시 만듦 | — |
| 시험 | `tests/test_mvp_runtime_read_bridge_projection.py`(새 파일, 23건), `tests/test_mvp_runtime_scheduler_factory_child.py`(3개 시험, 8건 추가) | §4.13.7 X-0–X-12와 e2 시험 | — |

**바꾸지 않은 것:**
- `docker-compose.yml`(a1 서비스, `:ro`, 로그 마운트), 정책 파일, `schemas/`, `.claude`, `CLAUDE.md`
- 실행기, 호스트, Docker
- hermes 인스턴스의 동작: 플래그가 없으므로 기존 `test_mvp_runtime_read_bridge.py`가 그대로 통과한다(X-10)

**되돌리기:**
`git -C /root/wt-option-c restore -- runtime/mvp_runtime/store_reads.py runtime/mvp_runtime/read_bridge.py runtime/mvp_runtime/read_bridge_cli.py runtime/mvp_runtime/scheduler.py tests/test_mvp_runtime_scheduler_factory_child.py docs/DIAGNOSTIC_CODE_INDEX.md`를 실행하고 `tests/test_mvp_runtime_read_bridge_projection.py`를 지운다.

**시험 결과(이 worktree, 실제 실행):**
- 새 시험 23건 통과. 응답 투영 결함 주입(스키마 검사 끔 + 원본 status 흘림) 시 6건 실패.
- e2 시험 포함 factory 자식 시험 14건 통과. 이전 형식으로 되돌리고 잔존 정리를 끄면 5건 실패.
- read door 주변 회귀(read·projection·socket hardening·정책 조항·env 경계·egress 명부·domain isolation·readiness 2종·문서 lifecycle) 968건 통과.
- scheduler·scheduler CLI·factory 자식·data review·cycle 306건 통과.
- 전체: `scripts/ops/test_run.sh --gate`(worktree 안 로컬 Core 활성화, 커밋 없음): 10,950 passed, 2 skipped, 0 failed, `TEST_RUN_EXIT=0`. 1차 실행은 진단 코드 색인 시험 2건이 실패했다(색인이 낡음, `PROJECTION_UNKNOWN`을 두 모듈에서 raise). read_bridge가 `store_reads.require_projection`을 부르도록 고치고 `docs/DIAGNOSTIC_CODE_INDEX.md`를 생성 스크립트로 다시 만든 뒤 2차에서 통과했다
- Release Gate: PASS(check-only, 같은 실행)
- X-5b는 실행으로 확인했다: 깨진 원장 줄은 `PersistenceError LEDGER_UNREADABLE`이고 거부 문구에 줄 내용이 없다(이전 NOT_VERIFIED).

**아직 아닌 것:**
- 운영 검증(O-1–O-9) 전부
- compose 반영
- Claude 계정
- 실제 `:ro` 마운트
- **Claude의 read-bridge 접근 게이트는 여전히 BLOCKED다.** 코드만 있고 G5–G6이 없다.

#### 4.17.5 대기 결정 목록 (돌아와서 "기본값대로"로 답할 수 있게 권고 기본값을 붙인다)

| # | 결정 | 권고 기본값 | 막고 있는 것 |
|---|---|---|---|
| 1 | §4.17.2의 G2 해석(저장소 안 구현, 커밋 없음)을 유지할지 | 유지 | 되돌리면 §4.17.4의 되돌리기 명령 |
| 2 | Claude 계정 uid·gid | 전용 계정 `claude`, uid 2000, gid 2000(0·10000·10001과 다름) | a1 compose 리터럴, C5 |
| 3 | Claude 인스턴스 소켓의 호스트 경로 | `/run/thomas-claude/read.sock`, 디렉터리 0750, 그룹 `claude` | a1 compose, O-1 |
| 4 | 요청 로그(d2) 위치 | 호스트 `/var/log/thomas-claude-read/requests.jsonl`(상태 루트 밖). 컨테이너에 쓰기 가능으로 마운트하고, 소유는 uid 10001, Claude는 쓰기 불가 | d2 compose, O-7 |
| 5 | 진단 파일(e-1) 경로 | 구현값 `.runtime_governance_state/diagnostics/factory_child_errors.jsonl` 유지 | 없음(구현됨) |
| 6 | 투영 스키마를 `schemas/`에 둘지 | 코드 안에 둔다(지금 상태). 새 Schema 파일은 Reuse-first 예외가 필요하다 | 없음 |
| 7 | 커밋·PR 방식 | PR 3개로 나눈다. ① 문서(설계·readiness·history·STATUS) ② read door 코드(c1·f2·a1 코드·d2) ③ scheduler e2. ③은 Option C와 독립이다 | 병합, CI 5개 |
| 8 | e2를 crypto 연구 중단(CLAUDE.md) 예외 "bug fixes"로 볼지 | 그렇게 본다. 판단 규칙을 바꾸지 않고 노출만 줄이는 변경이다 | PR ③ |
| 9 | D-2 승인 채널 | R2(SSH 서명) | 실행기 구현 |
| 10 | D-4 `inspect_candidate`를 승인 없이 | 허용(env·Mounts 제외) | 실행기 구현 |
| 11 | D-10 실행기 계정 | E1(root) | 실행기 구현 |
| 12 | 실행기 설치 위치 | `/opt/thomas-deploy/<sha>/`, root 소유 | 실행기 설치(C6) |
| 13 | D-16 기동 배너 확인 | 대안 1(P1–P8) | 실행기 구현 |
| 14 | D-14 W-11·W-12 / W-13 | (a) Claude는 intake를 돌리지 않음 / 진입 가능 여부는 read-bridge, 프로세스 `ready`는 D | 분류 확정 |
| 15 | W-30 백업 스크립트 변경(§4.15.3) | C5에서 승인 | C5 |
| 16 | 그 밖의 결정 D-5–D-9, D-12, D-13, D-15 | §8의 권고 기본값 | 각 단계 |
| 17 | 실행기 구현 착수(G2) | D-2·D-4·D-10이 정해지면 C3 범위로 | D-1·D-3 구현 |
| 18 | C4 비운영 검증 환경(D-8) | 임시 VM 또는 rootless 컨테이너 안 systemd | N-1–N-6 사전 시험 |

### 4.18 보안 검토 반영 (2026-10-15)

**근거:** 2026-10-15 정적 보안 검토(R-01–R-10, E-01–E-04, S-1–S-4)와 같은 날 Thomas의 지시.
- **지시 내용:** R-01 대안 (a) 채택(단일 보안 프로필), R-02 감사 범위(`PEER_NOT_PERMITTED`·`PEER_CREDENTIALS_UNAVAILABLE`·디코딩 실패·`BRIDGE_BUSY` 기록), S-1·S-2 문구 정정
- **범위:** 저장소 안 코드·문서·합성 시험에 한정한다.
- **주지 않는 것:** 운영 권한, 과거 설계 결정의 최종 승인, PR·병합 권한

**유지되는 상태:** Claude read-bridge 접근 BLOCKED, 운영 적용 HOLD, 실행기 미구현, 커밋·PR 미실행.

#### 4.18.1 상태 용어

| 상태 | 뜻 | 이 문서에서 해당하는 것 |
|---|---|---|
| RECOMMENDED | 권고가 적혀 있을 뿐이다 | §8의 미결정 항목 대부분 |
| PROVISIONALLY_ACCEPTED | 권고 일괄 수용 지시를 근거로 구현 방향을 해석했고, 항목별 명시 확정은 대기 중이다 | 2026-10-16 기준: D-1 대안 C, D-3 §4.3 범위, D-11 b3. (D-11 a1·c1·d2·f2·e2(e-1–e-3)는 2026-10-16에 명시 확정됐다, §4.24.2) |
| IMPLEMENTED_PENDING_REVIEW | 저장소에 구현되고 시험을 통과했지만 검토·확정 전이다(커밋 없음) | **없음(2026-10-16 기준).** 이 상태였던 PR-B 내용과 PR-C 내용(e2, E-02–E-04)은 리뷰를 거쳐 main에 병합됐다(MERGED `8979e702`, `0b6fd69c`, 미배포) |
| EXPLICITLY_APPROVED | Thomas가 그 항목을 이름으로 지시했다. 범위는 지시 문구 그대로다 | Option A(2026-10-12). R-01 (a)·R-02 범위·S-1·S-2(2026-10-15, 저장소 안 구현 한정). 2026-10-15 2차 A–F(§4.19: 2개 유지·4개 보류, R-03 계약 설계, 무응답 연결 공백 유지, R-07, R-08 문서화, E-01–E-04 후속 유지). 2026-10-15 3차 R3-5·R3-1·R3-2 구현과 R3-3·R3-4 보류(§4.20, 저장소 안·합성 시험 한정). 2026-10-16: D-11 a1(코드 부분)·c1·d2·f2 저장소 구현 범위 확정, PR-B 생성 승인, PR-B(#1223) 지정 HEAD squash 병합 승인. 2026-10-16(2): D-11 e2(저장소 보안 구현), crypto 예외(보안 버그 수정만), E-01 E1-a(문서), E-02–E-04(저장소 보안 수정) |
| OPERATIONALLY_AUTHORIZED | 운영 반영을 승인받았다 | **없음** |

PR 상태(미생성/OPEN/MERGED)는 위 상태 용어와 별개로 적는다. MERGED는 main에 들어갔다는 뜻일 뿐 운영 권한이 아니다.

- 시험 통과(③ PASS)와 Release Gate PASS는 위 상태 어느 것도 올리지 않는다.
- EXPLICITLY_APPROVED라도 그 지시가 운영 권한을 주지 않으면 운영 단계로 넘어가지 않는다.

#### 4.18.2 R-01·R-02 구현 (IMPLEMENTED_PENDING_REVIEW)

**R-01 — 단일 프로필.** 파일은 `runtime/mvp_runtime/read_bridge.py`다.
- **프로필 정의:**
  - `DoorProfile(name, reads, projection)`는 frozen dataclass다.
  - `PROFILES`는 `MappingProxyType`이고 `claude` 하나만 있다.
  - `claude` 프로필은 `reads={schedules, scheduler_events}`, `projection=v1`이다.
- **조립 금지:** `apply_read(profile=)`는 `PROFILES`가 가진 바로 그 객체(identity)만 받는다. 값이 같은 다른 객체, 문자열, dict는 `DOOR_PROFILE_INVALID`로 거부한다.
- **정합성 검사:** 매 사용마다 확인한다.
  - reads가 비어 있지 않은 frozenset이고 `_PROFILE_ADMISSIBLE_READS` 안에 있어야 한다.
  - projection이 정의된 것이어야 한다.
  - 하나라도 어긋나면 `DOOR_PROFILE_INVALID`다. import 시점에는 검사하지 않는다(import 부작용 없음).
- **`open_door(profile=, request_log=)`:**
  - 알 수 없는 이름 → `DOOR_PROFILE_UNKNOWN`
  - 로그 없음 → `REQUEST_LOG_REQUIRED`
  - `MVP_BRIDGE_CLIENT_UID`가 비어 있음 → `READ_UID_ALLOWLIST_REQUIRED`. bind 뒤 transport가 다시 읽은 값으로 한 번 더 확인한다.
  - 프로필 없이 로그만 줌 → `DOOR_PROFILE_REQUIRED`
  - listen 전에 `DOOR_OPENED` 행을 쓰고, 쓰지 못하면 listen하지 않는다(`REQUEST_LOG_UNAVAILABLE`).
  - 보안 조건을 하나씩 끄는 인자는 없다.
- **CLI:** `--profile`과 `--request-log` 두 개뿐이다. 2026-10-13의 `--reads`·`--projection`·`--require-uid-allowlist`, `resolve_allowed_reads`, `READS_SUBSET_INVALID`는 없앴다(커밋된 적 없음).
  - 프로필 배너: `profile=claude, projection=v1, request_log=on, uid_allowlist=required`
  - 프로필이 없으면 배너와 door는 이전과 바이트 단위로 같다.
- **R-05:** 프로필 door는 모르는 이름이든 프로필 밖 읽기든 같은 `VERB_NOT_PERMITTED`로 거부하고, 자기가 서비스하는 읽기만 나열한다.

**R-02 — 모든 응답의 감사.**
- **방식:** `_AuditedHandler`는 `socket_door._Handler`의 하위 클래스다. `_reply`를 재정의해 보내기 직전의 모든 응답을 기록한다.
  - 대상: 정상 응답, 타입이 있는 거부, `PEER_NOT_PERMITTED`·`PEER_CREDENTIALS_UNAVAILABLE`, 디코딩 실패
  - busy 거부는 `_ProfileDoor._refuse_busy`가 기록한다.
- **`socket_door`는 고치지 않았다.** 비공개 이름(`_Handler`, `_reply`)과 결합하지만 공개 계약은 바뀌지 않는다. 결합이 깨지면 `test_every_answer_the_transport_sends_is_logged`가 실패한다.
- **행 형식:** `{at, peer_uid, read, outcome}`
  - `peer_uid`: 커널 `SO_PEERCRED`의 값이다. 알 수 없으면 null이고 추정하지 않는다.
  - `read`: `_READS`의 이름, `<not_a_read>`, 또는 디코딩 전이면 null이다(R-04).
  - `outcome`: `ok` 또는 reason code 형식의 코드. 그 밖의 값은 `UNKNOWN`이다.
  - 인자, 응답 내용, 요청 본문은 기록하지 않는다.
- **thread-local 처리:** 읽기 이름은 handler의 `setup`에서 초기화하고, `_reply`가 꺼내면서 비운다. 시험은 작업 스레드 1개로 같은 스레드 재사용을 강제해 확인한다.
- **기록 실패:** 응답을 `REQUEST_LOG_UNAVAILABLE`로 바꾼다. 그 문구에는 경로도 예외도 없다. busy 거부는 기록에 실패해도 그대로 거부한다(응답 내용 없음).
- **기록하지 않는 것:** 응답이 나가지 않은 연결이다. 프레임이 시간 초과되거나 크기 상한을 넘거나 peer가 먼저 끊으면 `socket_door._Handler.handle`이 응답 없이 끝난다. 이것까지 기록하려면 공통 transport를 바꿔야 해서 하지 않았다(§4.18.6 #2).

**시험:** `tests/test_mvp_runtime_read_bridge_projection.py`, 41건(2026-10-15).
- **결함 주입 16종이 모두 실패를 일으킨다.** 주입한 결함:
  - 보안 조건 제거: 허용 목록 필수, 부분집합, 투영, 로그 필수, 로그 단독 허용
  - 감사: handler 미설치, 기록 실패 시 응답, thread-local, busy 기록, 자유 텍스트 기록
  - 프로필: 같은 값의 다른 객체 수용, 정합성 검사, 보류 읽기 편입
  - 그 밖: 전체 표 노출, `$` 앵커, bind 뒤 거부 시 소켓 파일 정리

#### 4.18.3 R-03 — 나머지 4개 읽기의 응답 (정적 소스, 2026-10-15)

| 읽기 | 응답 구성 | 민감 가능성(근거) | 판정 |
|---|---|---|---|
| `heartbeat` | `data.services[*]`: service, status, detail, age, heartbeat_at, interval, pid. busy 표지가 있으면 그 dict를 통째로 넘김 | **예외 문구:** 기록을 파싱하지 못하면 `detail`에 `"<type>: <message>"`가 들어간다(`heartbeat.py:115-116`, `:140-141`). 권한 오류면 절대경로도 들어갈 수 있다. busy 표지 키가 자동으로 노출된다(`:143`, `:145`) | 원본 그대로 노출 |
| `runtime_status` | 응답 문구(`control.py:248-266`)와 `data={mode, changed, action}` | **식별자·자유 텍스트:** 저장된 `updated_by`(운영자 id)와 `reason`(운영자가 입력한 문구)이 응답 문구에 들어간다(`control.py:604-607`) | 원본 그대로 노출 |
| `crypto_readiness` | `data`는 대부분 필드별로 구성된다. 단 저장 기록의 `execution_stage`, 코드 목록, `approval_id`는 그대로 넘어간다. 응답 문구에는 저장된 자유 텍스트(`reason`, mark `status`·`error` 등)와 env 이름이 들어간다 | 예외는 클래스 이름만 들어간다. **door 컨테이너에 계정 env가 있으면 `read_account`가 거래소에 GET을 보낸다**(`live_readiness.py:800-802`). 컨테이너 env는 NOT_VERIFIED | 원본 그대로 노출, 네트워크 가능성 |
| `holdings_status` | `data`는 snapshot 키 중 `record_type`·`written_at`만 뺀 전부다(`holdings/store.py:352`, 제외 목록 방식) | 계좌는 `****`와 끝 4자리로 마스킹된다. `notes`는 자유 텍스트다. snapshot에 키가 새로 생기면 자동으로 노출된다 | 원본 그대로 노출 |

**근거 등급:**
- 위 4행은 SOURCE_SUPPORTED다. 하위 에이전트가 정적으로 추적했고, 핵심 줄(`heartbeat.py:105-146`, `control.py:248-266`·`:600-610`, `holdings/store.py:330-355`, `live_readiness.py:795-822`)은 직접 확인했다.
- 실제 운영 응답은 보지 않았다.

**조치(IMPLEMENTED_PENDING_REVIEW):**
- Claude 프로필은 투영된 2개 읽기만 서비스한다.
- 위 4개는 `_PROFILE_HELD_READS`로 따로 두었다. 프로필에 넣으면 정합성 검사가 거부한다.
- 이 조치는 f2의 6개를 2개로 줄인 것이다. "프로필은 민감한 읽기의 원본을 반환하지 않는다"(2026-10-15 지시)를 지키는 fail-closed 선택이다.
- 4개를 다시 넣으려면 읽기마다 응답 계약이 필요하다. 이것은 설계 결정이라 하지 않았다(§4.18.6 #1).

**기존 시험의 공백:** heartbeat와 holdings `data`의 닫힌 키 집합, `detail`에 예외 문구가 없음을 고정하는 시험이 없다.

#### 4.18.4 LOW 결함 처리

| ID | 처리 | 위치 |
|---|---|---|
| R-04 | 수정. 알 수 없는 이름은 `<not_a_read>`로 기록 | `read_bridge._audit_read` |
| R-05 | 수정. §4.18.2 참조 | `apply_read` |
| R-06 | 수정. 투영 정규식과 스키마 패턴의 `$`를 `\Z`로. 끝에 줄바꿈이 붙은 값은 빠지고 `v1-partial`이 된다 | `store_reads.py` |
| R-09 | 수정. `schedules`의 `as_of`에 시각 패턴을 붙였다. "스키마는 모양만 검사하고, 통제는 닫힌 목록에서 행을 다시 만드는 데 있다"를 코드 주석과 이 절에 적었다 | `store_reads.py` |
| R-10 | 수정. `apply_read`의 `projection`·`allowed_reads` 인자를 없애고, 등록된 프로필 객체만 받는다 | `read_bridge.py` |
| R-07 | 2026-10-15 2차에 수정했다(§4.19.1). `O_NOFOLLOW`, descriptor 검사, sidecar lock 제거. 공통 `filelock`은 그대로 | `read_bridge._open_request_log` |
| R-08 | **수정하지 않음**. 회전·보존·손상 처리 정책은 미정이다. 현재 동작: 행마다 열고 닫으므로 rename 회전과 copytruncate 둘 다 동작한다. fsync가 없다. 잘린 행을 감지하지 않는다(읽는 쪽 없음) | — |

#### 4.18.5 scheduler e2 후속 (이번에 바꾸지 않음)

e2는 read-bridge와 별개로 검토할 수 있게 그대로 둔다(PR ③ 후보).

| ID | 내용 | 권고 |
|---|---|---|
| E-01 | 운영자가 진단 파일을 읽을 경로가 정해지지 않았다(CLI·read 없음). 알림에는 이제 예외 타입만 간다 | 운영자 전용 열람 방법 결정(호스트 root 열람 또는 scheduler CLI의 읽기 명령) |
| E-02 | 진단 기록이 `OSError`만 잡는다. 다른 예외는 terminal event보다 먼저 빠져나가 tick loop를 끝낸다. 손으로 만든 spool 파일에서만 도달한다 | `except Exception`, 또는 진단 기록을 terminal event 뒤로 |
| E-03 | 콜론이 없는 식별자 모양 문자열이 status에 그대로 들어간다 | 콜론이 없으면 알려진 상수만 허용 |
| E-04 | 진단 파일 0644, 디렉터리 0755(umask). 보호는 상태 루트 0700에 기댄다. 백업 포함 여부는 NOT_VERIFIED | 생성 시 0600, 디렉터리 0700 |

#### 4.18.6 결정 대기 (권고 기본값)

| # | 결정 | 권고 기본값 |
|---|---|---|
| 1 | R-03의 4개 읽기를 Claude 프로필에 넣을지 | 2026-10-15 2차: 2개 유지·4개 보류(EXPLICITLY_APPROVED). 계약은 설계됐다(§4.19.2). 추가는 별도 승인 대기 |
| 2 | 응답 없이 끝난 연결(시간 초과·크기 초과·peer 끊김)도 기록할지 | 2026-10-15 2차: 기록하지 않음을 유지(EXPLICITLY_APPROVED). 공백은 §4.19.4 |
| 3 | R-07 `O_NOFOLLOW` | 2026-10-15 2차: 채택·구현(§4.19.1) |
| 4 | R-08 로그 회전·보존 | 미결정. 현재 상태와 필요한 운영 정책을 §4.19.5에 적었다. 보존 기간은 Thomas가 정한다 |
| 5 | E-01–E-04 | PR ③ 전에 E-02·E-03·E-04는 수정, E-01은 열람 방법 결정 |
| 6 | PROVISIONALLY_ACCEPTED 항목(§4.17.1)의 항목별 확정 | 항목마다 따로 답한다. 한 항목의 확정은 다른 항목을 확정하지 않는다 |

### 4.19 권고 결정 반영과 R-03 응답 계약 (2026-10-15, 2차)

**근거:** Thomas 2026-10-15 2차 지시. 아래 A–F만 EXPLICITLY_APPROVED이고, 범위는 저장소 안의 설계·보안 코드·합성 시험·문서다.

| # | 지시 | 이 절 |
|---|---|---|
| A | Claude 프로필은 `schedules`·`scheduler_events` 2개만 유지한다. 4개는 응답 계약 확정과 보안 검증 전까지 보류한다 | 유지(§4.18.3) |
| B | 보류된 4개의 응답 계약을 설계하고 합성 fixture로 검증한다. 프로필에 실제로 추가하는 것은 별도 승인 | §4.19.2–§4.19.3 |
| C | 응답 없이 끝난 연결 3종의 감사 공백을 유지하고 문서에 적는다. `socket_door`는 고치지 않는다 | §4.19.4 |
| D | R-07 `O_NOFOLLOW` | §4.19.1 |
| E | R-08은 문서화만 한다. 보존 기간·회전·삭제는 정하거나 구현하지 않는다 | §4.19.5 |
| F | E-01–E-04는 후속으로 유지하고 scheduler 동작은 바꾸지 않는다. 진단 파일을 읽으라고 Claude에게 Docker·root를 주지 않는다 | §4.19.6 |

**바뀌지 않는 것:**
- D-1, D-3, D-11 a1·b3·c1·d2·e2·f2는 PROVISIONALLY_ACCEPTED 그대로다(2026-10-15 2차 시점. 2026-10-16에 a1·c1·d2·f2·e2가 명시 확정됐다, §4.24.2).
- 다음은 승인되지 않았다: 운영 호스트 계정, UID/GID, 소켓 경로, 로그 디렉터리 경로, compose, 실행기, 승인 채널, 커밋·PR·병합·배포.
- Claude read-bridge 접근은 BLOCKED, 운영 적용은 HOLD다.

#### 4.19.1 R-07 — 요청 로그를 링크를 따라가지 않고 열기 (IMPLEMENTED_PENDING_REVIEW)

**영향 범위 분석:**
- 이전 기록 방식은 공통 `filelock.locked`의 sidecar `requests.jsonl.lock`을 썼다. 이 파일은 `Path.open("a")`로 열려 symlink를 따라가고, 부모 디렉터리까지 만든다(`filelock.py:93-94`).
- `filelock`은 43개 모듈, 834개 호출 지점이 공유한다. 이것을 바꾸면 모든 저장소의 잠금 동작이 바뀌므로 **고치지 않았다.**
- 대신 요청 로그만 sidecar를 쓰지 않고, 로그 파일 자신의 descriptor에 `fcntl.flock`을 건다. 대기 상한과 간격은 `filelock`의 상수를 그대로 쓴다.

**구현:** `read_bridge._open_request_log`, `_log_request`

| 항목 | 동작 |
|---|---|
| 열기 플래그 | `O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, mode 0640(umask 적용) |
| 마지막 경로 요소가 symlink | `ELOOP`로 거부한다. 링크 대상은 쓰지도 만들지도 않는다(대상이 없는 symlink 포함) |
| FIFO | reader가 없으면 열기 자체가 실패한다. reader가 있으면 아래 descriptor 검사가 거부한다. `O_NONBLOCK` 덕분에 handler 스레드가 멈추지 않는다 |
| descriptor 검사 | 일반 파일, 링크 수 1(hard link 거부), 소유자 = 이 프로세스의 euid, 그룹·기타 쓰기 권한 없음. 경로가 아니라 열린 descriptor를 검사하므로 확인과 열기 사이의 경쟁이 없다 |
| 디렉터리 | 만들지 않는다. 없으면 거부한다. 이전에는 `filelock`이 부모 디렉터리를 만들었다(동작 변경) |
| sidecar | 없다. `.lock` 파일을 만들지 않는다 |
| 쓰기 | 한 줄을 `os.write` 한 번으로 쓴다. 덜 써지면 거부한다 |
| `O_NOFOLLOW` 또는 `fcntl`이 없는 플랫폼 | 거부한다(Windows). 프로필 door는 그 플랫폼에서 어차피 listen할 수 없다 |
| 실패 시 | 모두 `REQUEST_LOG_UNAVAILABLE`이다. 문구에 경로·예외가 없다. 응답을 보류하고, door가 열리는 시점이면 listen하지 않는다 |

**보호하지 않는 것:** 마지막 경로 요소만 보호한다. 그 위 디렉터리는 소유자를 신뢰한다. 로그 디렉터리의 소유·권한은 운영 결정이다(§4.17.5 #4, O-7).

**시험:** 13건 추가, 모두 합성 `tmp_path`.
- 대상: 정상 생성과 추가 쓰기(sidecar 없음, 이웃 파일 불변), symlink, 대상이 없는 symlink, hard link, 그룹 쓰기, 기타 쓰기, 디렉터리, FIFO, reader가 있는 FIFO, 다른 소유자, 디렉터리 없음, 미지원 플랫폼, symlink 경로로 door 열기
- **결함 주입 8종이 모두 실패를 일으킨다.** 주입한 결함: `O_NOFOLLOW` 제거, 플랫폼 거부 제거, `O_NONBLOCK` 제거(시험이 멈춤), 일반 파일 검사·링크 수·소유자·권한 검사 각각 제거, 디렉터리 자동 생성

#### 4.19.2 R-03 응답 계약 v1 (설계, SCRATCH 검증)

**공통 규칙:**
- 응답은 원본 dict나 렌더링된 문구가 아니라 타입이 있는 출처에서 허용 목록대로 새로 만든다.
- 알 수 없는 enum 값은 `UNKNOWN`으로 바꾸고 행을 `v1-partial`로 표시한다.
- 형식이 틀린 선택 필드는 빼고 `v1-partial`로 표시한다.
- 출처에 새 필드가 생겨도 계약에 없으면 나가지 않는다. 닫힌 JSON Schema(`additionalProperties: false`, `\Z` 앵커)가 그 뒤를 받친다.
- **오류 계약:** `{ok:false, reason_code, reason}`. reason은 코드별로 고정된 문구이고, 상류 예외 메시지는 전달하지 않는다. 계약에 없는 코드는 `READ_UNAVAILABLE`이다.
- 응답을 만드는 동안 네트워크·파일을 열지 않는다. 출처 객체는 호출자가 넘긴다.

**heartbeat** (우선순위 1)

| 구분 | 내용 |
|---|---|
| 원본 | `services[*]`: service, checked_at, age_seconds, status, detail, stale_after_seconds, heartbeat_at, interval_seconds, pid, busy(통째로), overrun. 그리고 as_of, all_fresh |
| Claude에 필요한 것 | 세 loop가 살아 있는지, 늦었는지, 지금 어떤 작업이 기한을 넘겼는지 |
| 노출 금지 | `detail`(파싱 실패 시 `"<type>: <message>"`, 경로 가능), `pid`, busy의 나머지 키 |
| 허용 필드 | `service`(3개 enum 또는 UNKNOWN), `status`(FRESH/STALE/MISSING/UNREADABLE 또는 UNKNOWN), `unreadable_part`(record/busy_mark), `age_seconds`·`stale_after_seconds`·`interval_seconds`(0 이상의 수), `heartbeat_at`(ISO), `busy{kind(scheduler.KINDS), overrun(bool), started_at, deadline_at}`, `projection`. 최상위: services, all_fresh, as_of, projection |
| null·누락 | 수치가 null이면 생략한다. 형식이 틀리면 생략하고 partial로 표시한다. 파일이 없으면 status MISSING이다 |
| 전제(결정 필요) | `unreadable_part`는 지금 `detail` 문구의 접두어("busy mark:")로만 알 수 있다. 저장소 구현 때는 `check_heartbeat`이 구조화된 코드를 내도록 바꾸는 것을 권고한다 |

**runtime_status** (2)

| 구분 | 내용 |
|---|---|
| 원본 | 응답 문구: mode, execution, updated_by, updated_at, reason, live entries, halt, stop_requested_task_ids. `data`: mode, changed, action |
| Claude에 필요한 것 | 실행 가능 여부, 진입 무장 여부, halt 단계, 상태 파일 손상 여부 |
| 노출 금지 | `updated_by`(운영자 id), `reason`(운영자 자유 문구, 손상 시 시스템 문구), task id 목록 |
| 허용 필드 | `mode`(ACTIVE/PAUSED/KILLED/UNKNOWN), `execution_allowed`, `trading_armed`, `halt_level`(SOFT/HARD/null), `fail_closed`, `stop_requested_count`(정수), `updated_at`(ISO, 선택), as_of, projection |
| 출처 | 렌더링된 응답 문구가 아니라 `ControlStore.load()`의 `ControlState`다. 지금 read 경로는 문구만 만들므로, 저장소 구현 때 이 출처로 읽는 경로가 새로 필요하다 |

**crypto_readiness** (3)

| 구분 | 내용 |
|---|---|
| 원본 | `readiness_data` 전체(`readiness_model.py:526-558`). 응답 문구에는 저장된 자유 텍스트와 env 이름이 들어간다 |
| 노출 금지 | env 이름·범위, `checks`의 detail, `execution_stage`의 stage_id·record_sha256·policy_version·**approval_id**, venue_contract의 error·status·failed_checks, recorded_gate의 `status` 문구, `guard_dry_run_status`, 구성요소의 extra(stage·uncovered 등), 코드 형식이 아닌 reason |
| 허용 필드 | `live_entry_possible`(true/false/null), `components[*]{name(READINESS_COMPONENTS 11개), ok(true/false/null), reason_code(코드 형식일 때만)}`, `execution_stage`(LADDER enum), `stage_admits_entry`, `recorded_gate{known, open, recorded_at, stale}`, `armed_strategies`(정수/null), `submitted_today`(정수/null), as_of, projection |
| 뺀 것 | `infrastructure_ready`. 이 값은 그 프로세스의 env를 판정하므로 door에서 읽으면 뜻이 달라진다(W-13, §4.8.3) |
| 전제(결정 필요) | `build_readiness`는 계정 env가 있으면 거래소에 GET을 보낸다(`live_readiness.py:800-802`). Claude door에서는 이 builder를 그대로 부르면 안 된다. 기록된 상태만 읽는 builder(새 코드)가 필요하다. 이번에는 builder를 실행하지 않았다 |

**holdings_status** (4)

| 구분 | 내용 |
|---|---|
| 원본 | snapshot 키 중 `record_type`·`written_at`만 빼고 전부: account(마스킹), broker, latency_ms, 금액 6종, weights, holding_count, partial, notes, cash_flows, allocation, readiness, combined, 그리고 앞으로 생길 키. snapshot이 없으면 `{available, reason_code, last_attempt}` |
| 노출 금지 | 원본 snapshot 전체, 계좌, 금액·비중·손익, notes, cash_flows, last_attempt, 새 키 |
| 허용 필드 | `available`, `reason_code`(HOLDINGS_SNAPSHOT_MISSING만), `snapshot_as_of`, `age_seconds`, `stale`, `partial`, as_of, projection. **신선도만** 담는다 |
| 결정 필요 | 금액(합계 등)이나 `holding_count`를 Claude에게 줄지. 기본값은 주지 않음 |

**검증(SCRATCH, 저장소 밖):**
- 참조 구현과 시험 18건이 통과했다.
- 원본 fixture: heartbeat·runtime_status·holdings는 실제 상류 함수(`check_heartbeat`, `ControlStore.load`, `load_holdings_view`)를 합성 `tmp_path` 상태 위에서 실행해 얻었다. 원본에 표지가 실제로 있음을 먼저 확인했다.
- crypto_readiness는 builder를 실행하지 않고 `readiness_data`의 키로 직접 만든 fixture를 썼다.
- 닫힌 목록을 소스 상수와 대조했다.
- **누출 주입 8종이 모두 실패를 일으킨다.** 주입한 누출: detail 전달, busy 통째 전달, reason 전달, 구성요소 reason 무검사, holdings 원본 전달, 오류 문구 전달, 스키마 열기, 스키마 검사 생략

#### 4.19.3 계약의 남은 노출 위험 (STEP 8)

| 계약 | 남는 정보 | 평가 |
|---|---|---|
| heartbeat | 각 loop의 마지막 시각, 지금 도는 작업 종류와 기한 | 운영 리듬이 드러난다. 내용·식별자는 없다 |
| runtime_status | 정지·halt 여부, 갱신 시각, 정지 요청 수 | 운영자가 언제 개입했는지 시각이 드러난다. 누가·왜는 없다 |
| crypto_readiness | 구성요소별 통과 여부와 코드, 단계, 무장 수, 오늘 제출 수 | 주문 가능성의 요약이다. 키·금액·심볼은 없다. 코드는 작성자가 정한 상수이고 형식 검사를 거친다 |
| holdings_status | snapshot 시각과 신선도 | 갱신 주기만 드러난다 |

- 네 계약 모두 자유 텍스트, 예외 메시지, 경로, env, 운영자 id, 계좌, 금액이 없다.
- **다시 프로필에 넣는 조건:** 저장소 구현, 시험·결함 주입, 운영 검증(O 항목)을 거친 뒤 Thomas가 별도로 승인한다.

#### 4.19.4 응답 없이 끝난 연결의 감사 공백 (C, 유지)

- **기록하는 것:** 정상 응답, 인증 거부(`PEER_NOT_PERMITTED`), 자격 확인 불가(`PEER_CREDENTIALS_UNAVAILABLE`), 디코딩 실패, `BRIDGE_BUSY`, `DOOR_OPENED`
- **기록하지 않는 것:** 다음 세 경우다. 셋 다 응답을 보내지 않으므로 나간 데이터는 없다. 연결 시도는 감사에서 보이지 않는다.
  1. 읽기 시간 초과
  2. 크기 상한을 넘은 프레임
  3. peer가 응답 전에 끊음
- **다시 정할 때:** 공통 `socket_door._Handler.handle`을 바꿔야 한다. 보안 요구가 바뀌면 그때 정한다.

#### 4.19.5 R-08 — 요청 로그 보존의 현재 상태 (결정 없음, 구현 없음)

| 항목 | 현재 |
|---|---|
| 보존 | 무기한 추가만 한다. 회전·삭제·압축 기능은 없다 |
| 증가량 | 행당 89–106바이트(합성 행 측정). 응답 하나당 한 행, door가 열릴 때 한 행. 하루 증가량 = 요청 수 × 약 100바이트. 실제 요청 빈도는 운영 전이라 알 수 없다 |
| 손상 | 덜 써지면 그 요청을 거부한다. 잘린 줄이 남을 수 있다. 앞의 행은 영향받지 않는다. 로그를 읽어 검증하는 쪽이 없어서 잘린 줄을 감지하지 않는다. fsync가 없어 전원 장애 때 마지막 행이 사라질 수 있다 |
| 기록 실패 | 응답을 보류한다(`REQUEST_LOG_UNAVAILABLE`). door가 열리는 시점이면 listen하지 않는다. busy 거부는 기록에 실패해도 거부로 나간다 |
| rename 회전 | 다음 행은 새 파일을 만든다(`O_CREAT`). 회전 도구가 새 파일을 다른 소유자나 그룹 쓰기 권한으로 만들면 door는 이후 모든 요청을 거부한다(fail-closed, 가용성 손실) |
| copytruncate 회전 | 복사와 truncate 사이에 쓴 행이 사라질 수 있다(감사 누락). 이 로그에는 맞지 않는다 |
| 정해야 할 운영 정책 | 보존 기간, 회전 방식(rename 권고), 새 파일의 소유·권한, 디스크가 찼을 때 대응, 보관 위치와 열람자 |

#### 4.19.6 E-01–E-04 위험도와 의존성 (후속, scheduler 동작 변경 없음)

| ID | 위험도 | 내용 | 의존성 |
|---|---|---|---|
| E-01 | MEDIUM | 운영자가 진단 메시지를 볼 경로가 없다. 알림에는 예외 타입만 간다 | 운영자 열람 방법 결정. **Claude에게 Docker·root를 주지 않는다.** 열람은 Thomas(호스트) 또는 앞으로의 운영자 전용 경로 |
| E-02 | LOW | `OSError` 밖의 예외가 terminal event보다 먼저 빠져나가 tick loop를 끝낸다. 손으로 만든 spool 파일에서만 도달한다 | 없음. PR ③ 전에 고칠 수 있다 |
| E-03 | LOW | 콜론이 없는 식별자 모양 문자열이 status에 그대로 들어간다 | 없음 |
| E-04 | LOW | 진단 파일 0644, 디렉터리 0755. 백업 포함 여부는 NOT_VERIFIED | 권한값 결정(권고 0600/0700). 백업 범위 확인은 C5(W-30) |

#### 4.19.7 저장소 반영 시 확인할 것

- **Windows 시험의 skip 상한:** 새 시험 파일의 `@unix_only`·`posix_log` 표시로 Windows에서 건너뛰는 시험이 늘었다. `tests/skip_ceiling.json`의 `win32`(258)는 CI에서 측정해 PR에서 바꾸는 값이라 지금 고치지 않았다. PR 때 Windows CI의 실제 수로 갱신해야 한다.
- **결정 대기:** §4.18.6 #1은 이번 B에서 설계로 진행됐다(추가는 별도 승인). #2는 이번 C에서 유지로 정해졌다. #3은 이번 D에서 구현됐다. 새로 생긴 결정은 다음 표와 같다.

| # | 결정 | 권고 기본값 |
|---|---|---|
| R3-1 | heartbeat 계약을 저장소에 구현할지. `check_heartbeat`의 구조화된 실패 코드 포함 | 2026-10-15 3차: 구현(§4.20.2). 프로필 추가는 별도 승인 |
| R3-2 | runtime_status 계약의 출처(`ControlState`)로 읽는 경로를 만들지 | 2026-10-15 3차: 구현(§4.20.3). 프로필 추가는 별도 승인 |
| R3-3 | crypto_readiness용으로 네트워크를 쓰지 않는 기록 기반 builder를 만들지, 이 read를 Claude에게 계속 주지 않을지 | 2026-10-15 3차: 보류 유지. builder 미구현, 기존 builder 미실행 |
| R3-4 | holdings_status에서 금액·종목 수를 줄지 | 2026-10-15 3차: 주지 않음(금액·종목·보유 개수·계좌·snapshot·notes 미노출). read는 보류 유지 |
| R3-5 | 프로필 door의 오류 문구를 코드별 고정 문구로 바꿀지 | 2026-10-15 3차: 구현(§4.20.1) |

### 4.20 오류 응답 고정(R3-5)과 heartbeat·runtime_status 계약 구현(R3-1·R3-2) (2026-10-15, 3차)

**근거:** Thomas 2026-10-15 3차 지시.
- **채택(EXPLICITLY_APPROVED, 저장소 안 코드·합성 시험 한정):** R3-5, R3-1, R3-2
- **보류 유지:** R3-3(crypto_readiness), R3-4(holdings 금액·종목·보유 개수 미노출), R-08, E-01–E-04
- **승인되지 않은 것:** 새 read의 프로필 등록, 운영 접근, 커밋·PR·병합·배포
- **유지되는 상태:**
  - Claude 프로필: `schedules`·`scheduler_events` 2개
  - 보류: heartbeat·runtime_status·crypto_readiness·holdings_status 4개
  - 접근 BLOCKED, 운영 HOLD
  - D-1·D-3·D-11은 PROVISIONALLY_ACCEPTED 그대로

#### 4.20.1 R3-5 — 프로필 door의 오류 응답 (IMPLEMENTED_PENDING_REVIEW)

**노출 경로 분석(정적, VERIFIED):**

| 경로 | 지금까지 보내던 것 | 근거 |
|---|---|---|
| 타입이 있는 거부(`MvpRuntimeError`) | `str(exc)` 전체와 `exc.data`(dict면 그대로) | `socket_door.refusal_payload` |
| ledger 읽기 실패 | 위 경로로 `could not read …: [Errno …] '<절대경로>'`. 합성 시험에서 tmp 절대경로가 실제로 실렸다 | `jsonl.py:324`, `:365` |
| 예상하지 못한 예외 | 고정 문구이지만 `error_ref`에 door의 pid가 들어간다 | `socket_door.internal_error_payload` |
| 인증 거부 | `uid N is not permitted…` (peer 자신의 uid) | `socket_door.py:485-487` |
| 디코딩·envelope 거부 | 파서 메시지와 peer가 보낸 `proto` 값 | `socket_door.py:156`, `:193-195` |
| busy 거부 | door의 동시 처리 상한이 담긴 문구 | `socket_door._refuse_busy` |

**수정:** `read_bridge.py`. `socket_door`는 고치지 않았고, 그래서 다른 서비스의 오류 응답 계약은 바뀌지 않는다.
- **고정 문구 표:** `PROFILE_ERROR_TEXT`(코드 11개, 고정 문구)와 `profile_error_payload(code, echo_from)`
  - 형식은 기존 protocol 그대로 `{ok:false, reason_code, reason}`이다.
  - 목록 밖의 코드는 `READ_UNAVAILABLE`이 된다.
  - echo는 peer가 보낸 형식이 맞는 `proto`·`client_id`·`request_id`(공백 없는 출력 가능 ASCII 1–128자)만 한다.
- **`_AuditedHandler._reply`:** 감사 기록 뒤 모든 비정상 응답을 위 함수로 다시 만든다.
  - 감사 로그에는 실제로 발생한 코드를 남긴다.
  - 다시 만들지 못하면 상수 `BRIDGE_ERROR`로 응답한다(fail-closed).
  - 로그 기록에 실패하면 `REQUEST_LOG_UNAVAILABLE`이다.
- **`_ProfileDoor._refuse_busy`:** transport의 busy 문구 대신 고정 문구를 보낸다.
- **traceback:** 예상하지 못한 예외의 traceback은 기존대로 door 프로세스의 stderr(서비스 로그)로만 간다. 응답에는 `error_ref`도 없다. 운영자는 감사 로그의 시각과 `BRIDGE_ERROR` 행으로 서비스 로그를 찾는다.

**시험:** 15건.
- 대상:
  - 경로·비밀값 모양·여러 줄·traceback 모양 문구와 `data`를 담은 타입 거부
  - 실제 ledger 디렉터리 오류(절대경로)
  - 예상하지 못한 RuntimeError(pid 없음)
  - 투영 예외, 스키마 위반
  - 잘못된 JSON, proto 불일치, 없는 read, 프로필 밖 read, 인자 거부, 형식이 틀린 request_id
  - 인증 거부(uid 미노출), busy, 로그 실패, 재구성 실패 시 상수
  - 목록 밖 코드
  - hermes door의 기존 문구 불변
- **결함 주입 5종 모두 실패:** 재구성 생략, 목록 밖 코드 통과, request_id 무검증 echo, busy에 transport 문구, 상수 대체 제거
  - request_id 주입은 처음에 잡히지 않았다. 시험이 echo가 일어나지 않는 정상 응답 경로를 쓰고 있었기 때문이다. 거부 경로로 고친 뒤 잡혔다.

#### 4.20.2 R3-1 — heartbeat (IMPLEMENTED_PENDING_REVIEW, 프로필 미등록)

- **구조화된 실패 코드:** `heartbeat.inspect_heartbeat(service, now, root)`가 `(report, failure)`를 돌려준다.
  - `failure`는 `HEARTBEAT_RECORD_UNREADABLE`, `HEARTBEAT_BUSY_MARK_UNREADABLE`, 또는 `None`이다.
  - `check_heartbeat`는 그 report를 그대로 돌려주는 얇은 함수가 됐다. 기존 호출자인 `read_heartbeat`(hermes), `heartbeat_cli`가 받는 dict는 바뀌지 않았다(시험으로 고정).
- **투영:**
  - `store_reads.project_heartbeat_check(report, failure)`, `_projected_heartbeat`, `read_heartbeat(projection=)`
  - 닫힌 스키마 `_HEARTBEAT_DATA_SCHEMA`
  - 허용 필드는 §4.19.2 그대로다. `unreadable_part`는 `detail` 문구가 아니라 실패 코드에서만 나온다.
- **값이 이상할 때:**
  - enum 밖 값 → `UNKNOWN` + `v1-partial`
  - 음수·NaN·형식이 틀린 시각 → 빼고 `v1-partial`
  - UNREADABLE인데 코드가 없음 → `unreadable_part` 생략 + `v1-partial`
  - busy가 dict가 아니거나 overrun이 bool이 아님 → busy 생략 + `v1-partial`
- **연결:** `apply_read`는 투영하는 door일 때 heartbeat에 projection을 넘긴다. 이 read를 서비스하는 프로필은 없다.

#### 4.20.3 R3-2 — runtime_status (IMPLEMENTED_PENDING_REVIEW, 프로필 미등록)

- **읽기 경로:** `store_reads.read_runtime_status(control_store, now, projection)`
  - `ControlStore.load()`의 `ControlState`를 `project_runtime_status`로 투영한다.
  - 화면 문구나 운영자 메모는 파싱하지 않는다.
  - `load()`는 상태 파일을 읽는다. 파일이 없으면 control 이벤트 원장을 읽기 전용(`jsonl.read_objects`)으로 읽는다. 쓰기는 없다. 정적 확인했고, 합성 시험에서 tmp 트리가 바뀌지 않았다.
- **필드:**
  - 허용: mode, execution_allowed(mode에서 다시 계산), trading_armed, halt_level, fail_closed, stop_requested_count, updated_at, as_of, projection
  - 제외: updated_by, reason, task id, 그리고 상태 객체에 새로 생기는 필드
- **값이 이상할 때:** 알 수 없는 mode → `UNKNOWN` + `v1-partial`. 알 수 없는 halt → null + `v1-partial`. control store가 없으면 `CONTROL_STORE_UNAVAILABLE`.
- **연결:** `apply_read`의 `_CONTROL` 분기는 투영하는 door에서 status를 이 경로로 보낸다. 이 read를 서비스하는 프로필은 없다. 프로필이 없으면 기존 `/status` 문구 그대로다(시험 고정).

**R3-1·R3-2 시험:** `tests/test_mvp_runtime_store_reads_held_contracts.py` 14건, 모두 합성 `tmp_path`.
- 결함 주입 10종이 모두 실패를 일으킨다: detail·pid·busy 전달, 코드 무시, updated_by 전달, mode 미검사, 스키마 열기, 코드 뒤바꿈, 연결 2종 제거
- 연결 시험은 시험 안에서만 쓰는 임시 프로필을 monkeypatch로 넣는다. 등록은 아니다.

#### 4.20.4 R-07 추가 검토

| 항목 | 결과 | 심각도·조치 |
|---|---|---|
| flock 획득·해제 | descriptor에 걸고 `finally`의 close로 푼다. 대기 상한은 `filelock`과 같은 60초이고, 그동안 작업 스레드 하나를 쓴다 | 문제 없음 |
| 부분 쓰기 | 잘린 바이트가 남고, 다음 행이 그 뒤에 붙어 한 줄이 깨졌다 | **LOW, 수정.** 같은 lock 안에서 쓰기 전 크기로 `ftruncate`하고 거부한다. 시험 2건, 결함 주입으로 확인 |
| 동시 요청 | 요청마다 따로 열기 때문에 open file description이 따로이고, flock이 스레드 사이에서도 직렬화한다. 추가 쓰기 | 문제 없음 |
| fd 수명 | `finally`에서 close, `O_CLOEXEC` | 문제 없음 |
| 로그 교체 | 열린 뒤 rename돼도 행은 그 inode(이전 파일)로 간다. 다음 요청은 새 경로를 연다 | 문제 없음(회전 정책은 R-08) |
| hard link | 연 시점 링크 수가 1인지 검사한다. 그 뒤 링크가 생겨도 같은 inode라 다른 파일에 쓰지 않는다 | 문제 없음 |
| symlink | 마지막 요소만 보호(`O_NOFOLLOW`) | 디렉터리 신뢰는 운영 결정(§4.17.5 #4) |
| 디렉터리 소유 | 위 디렉터리는 소유자를 신뢰한다 | 운영 결정 |
| 생성 권한 | 0640에 umask 적용. 기존 파일은 그룹·기타 쓰기가 있으면 거부 | 문제 없음 |
| 기록 실패 시 응답 | `REQUEST_LOG_UNAVAILABLE` 고정 문구(R3-5) | 문제 없음 |

#### 4.20.5 Windows CI (NOT_VERIFIED)

- 새 시험 파일 `test_mvp_runtime_read_bridge_projection.py`(HEAD에 없음)의 71건 중 36건이 Windows에서 건너뛴다(`unix_only` 또는 `posix_log` 표시를 정적으로 센 값).
- `test_mvp_runtime_store_reads_held_contracts.py`는 0건이다.
- `tests/skip_ceiling.json`의 win32 상한 258은 바꾸지 않았다. PR의 Windows CI에서 측정해 바꾸는 값이다. 지금 그대로면 상한을 넘을 수 있다.
- 3.12 CI도 돌리지 않았다.

#### 4.20.6 새로 생긴 위험

| # | 내용 | 평가 |
|---|---|---|
| 1 | `apply_read`가 투영하는 door에서 heartbeat·runtime_status를 계약으로 보내도록 연결됐다. 프로필에 넣는 순간 동작한다 | 의도된 준비. 프로필 등록은 별도 승인. 보류 목록 시험이 등록을 막는다 |
| 2 | 프로필 door의 오류가 고정 문구가 되어, Claude는 원인을 알 수 없다 | 의도. 원인은 감사 로그의 코드와 서비스 로그로 운영자가 본다 |
| 3 | `error_ref`가 빠져서 응답과 서비스 로그를 잇는 참조가 없다 | 시각으로 잇는다. 운영자 불편(LOW) |
| 4 | `_AuditedHandler`가 `socket_door`의 비공개 이름(`_Handler`, `_reply`, `REQUEST_TIMEOUT_SECONDS`)에 기댄다 | 시험이 고정한다. `socket_door`가 바뀌면 시험이 실패한다 |
| 5 | `heartbeat.inspect_heartbeat`라는 공개 함수가 하나 늘었다 | 기존 결과는 그대로이고 추가만 했다 |

#### 4.20.7 결정 대기

| # | 결정 | 권고 기본값 |
|---|---|---|
| R3-6 | heartbeat·runtime_status를 Claude 프로필에 넣을지 | 넣지 않는다. 운영 검증 계획(O 항목 추가)을 먼저 정한다 |
| R3-3 | crypto_readiness | 보류 유지(지시대로). 기록 기반 builder 설계는 W-13과 함께 |
| R3-4 | holdings_status 계약 | 금액·종목·보유 개수·계좌·snapshot·notes 미노출을 기본 정책으로 기록. 구현은 별도 지시 |
| R-08 | 로그 보존·회전 | 미결정 |
| E-01–E-04 | scheduler 후속 | 유지 |
| W-1 | win32 skip 상한 | PR의 Windows CI에서 측정해 갱신 |

### 4.21 PR 준비: 결정 상태와 미승인 운영 항목의 분리 (2026-10-15)

PR을 검토할 때 무엇이 확정이고 무엇이 아닌지 이 절만으로 알 수 있게 모은다. 상태 용어는 §4.18.1을 따른다.

- PR은 셋이다: **PR-A** 문서, **PR-B** read-bridge, **PR-C** scheduler e2. 2026-10-16 기준: PR-B **MERGED**(#1223, `8979e702`), PR-C **MERGED**(#1224, `0b6fd69c`), PR-A는 이 문서다(§4.24).
- 병합 승인은 PR-B(#1223)와 PR-C(#1224)에, 각각 지정 HEAD의 squash로만 있었다(2026-10-16). PR-A의 생성과 병합은 따로 승인받는다.

#### 4.21.1 잠정 결정과 확정 현황 (2026-10-16 갱신. 표시가 없는 항목은 PROVISIONALLY_ACCEPTED)

| 항목 | 내용 | 저장소 구현 | 담긴 PR | 병합 전에 필요한 것 |
|---|---|---|---|---|
| D-1 | 대안 C(최소 전용 실행기) | 없음(실행기 미착수) | 문서(PR-A)만 | 항목별 확정. 실행기 구현은 D-2·D-4·D-10 이후 |
| D-3 | §4.3 범위 Reuse-first 예외 | 없음 | 문서(PR-A)만 | 항목별 확정 |
| D-11 a1 | Claude 전용 read-bridge 인스턴스 | 코드 부분만: `--profile`, uid 허용 목록 필수 | PR-B **MERGED**(`8979e702`) | **EXPLICITLY_APPROVED 2026-10-16(코드 부분, 저장소 범위).** compose 서비스는 미구현·미승인 |
| D-11 b3 | 지금은 `assistant_read` 조항(b1), 다음 정책 버전에서 b2 | 변경 없음 | — | 항목별 확정 |
| D-11 c1 | 응답 투영 v1 | `store_reads` 투영·스키마 | PR-B **MERGED** | **EXPLICITLY_APPROVED 2026-10-16(저장소 범위)** |
| D-11 d2 | 요청 감사 로그, 기록 실패 시 거부 | `read_bridge` 감사 로그(R-02, R-07) | PR-B **MERGED** | **EXPLICITLY_APPROVED 2026-10-16(저장소 범위).** 로그 경로·보존은 미결정 |
| D-11 e2 (e-1–e-3) | factory 실패 status는 타입만, 메시지는 진단 파일 | `scheduler`(E-02–E-04 포함) | PR-C **MERGED**(`0b6fd69c`) | **EXPLICITLY_APPROVED 2026-10-16(저장소 내부 보안 구현만)**, crypto 예외 APPROVED(보안 버그 수정만). 배포는 별도 승인 |
| D-11 f2 | Claude 인스턴스 읽기 6개 + `:ro` | 2개만(4개 보류는 2026-10-15 명시 지시) | PR-B **MERGED** | **EXPLICITLY_APPROVED 2026-10-16(2개 부분집합, 저장소 범위).** `:ro`는 운영 단계 |

**PR-B·PR-C를 병합하면** 위 항목의 구현이 main에 들어간다. 그래서 병합 승인은 그 PR이 담은 잠정 항목을 하나씩 확정하는 것과 함께 받아야 한다. 병합 승인 하나로 묶어서 해석하지 않는다. PR-B는 이 순서대로 진행됐다. 2026-10-16에 a1·c1·d2·f2를 먼저 확정했고, 그 뒤 병합을 따로 승인받았다. PR-C도 같은 원칙을 따른다.

#### 4.21.2 명시 지시 (EXPLICITLY_APPROVED, 저장소 안 코드·문서·합성 시험 한정)

| 날짜 | 항목 | 담긴 PR |
|---|---|---|
| 2026-10-12 | Option A(3-1·3-2 REJECT, B′ 미채택, Rollback·배포 NOT_GRANTED) | PR-A(`HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` §4.5) |
| 2026-10-15 1차 | R-01 (a) 단일 프로필, R-02 감사 범위, S-1·S-2 문구 | PR-B, PR-A |
| 2026-10-15 2차 | 2개 유지·4개 보류, R-03 계약 설계, 무응답 연결 공백 유지, R-07, R-08 문서화, E-01–E-04 후속 | PR-B, PR-A |
| 2026-10-15 3차 | R3-5 오류 고정, R3-1 heartbeat 계약, R3-2 runtime_status 계약(둘 다 프로필 미등록). R3-3·R3-4 보류 | PR-B, PR-A |
| 2026-10-16 | D-11 a1(코드)·c1·d2·f2 저장소 구현 확정. PR-B 생성 승인. PR-B #1223 지정 HEAD squash 병합 승인(→ MERGED `8979e702`) | PR-B |

이 지시들은 커밋·PR·병합·운영 권한을 주지 않는다.

#### 4.21.3 미승인 운영 항목 (어느 PR에도 없다)

| 항목 | 상태 | 근거 절 |
|---|---|---|
| Claude 계정 생성, uid·gid | 미결정·미승인 | §4.17.5 #2, C5 |
| Claude 소켓 경로·디렉터리 권한 | 미결정 | §4.17.5 #3, O-1 |
| 요청 로그 디렉터리·소유·보존·회전(R-08) | 미결정 | §4.17.5 #4, §4.19.5 |
| compose: a1 서비스, `:ro` 마운트, 로그 마운트 | 미구현·미승인 | §4.17.4 |
| heartbeat·runtime_status의 프로필 등록(R3-6) | 미승인 | §4.20.7 |
| crypto_readiness·holdings_status 계약 구현과 등록 | 보류 | §4.20.7 |
| 실행기(D-1 구현), 승인 채널(D-2), `inspect_candidate`(D-4), 실행 계정(D-10) | 미결정·미착수 | §8 |
| 호스트 단계 C5·C6, 부정 시험 N-1–N-6 | 미승인 | §6, §4.6 |
| 운영 검증 O-1–O-9 | NOT_TESTED | §4.16.3 |
| 운영 진단 열람 경로(E-01) | 방식은 E1-a로 승인됨(문서만, 2026-10-16). 실제 열람 실행·E1-b CLI는 미승인 | §4.23.3 |
| 배포, Rollback, 운영 preflight | NOT_GRANTED(Option A) | readiness §4.5 |

**Claude read-bridge 접근은 BLOCKED다.** 위 운영 항목과 G5–G6이 끝나야 풀린다(§4.16).

### 4.22 PR-B 병합 기록과 상태 동기화 (2026-10-16)

#### 4.22.1 PR-B #1223 — MERGED, 운영 미배포

| 항목 | 값 |
|---|---|
| PR | #1223 "Option C: Harden Claude read-bridge boundary" |
| Thomas 승인 | 2026-10-16: D-11 a1(코드 부분)·c1·d2·f2 저장소 구현 범위 확정, PR-B 생성 승인, 지정 HEAD의 squash 병합 승인(각각 따로) |
| 병합 | 2026-10-16T03:12:35Z, squash. squash SHA `8979e702f79ea10d2c4b51748229c09135e82441`, 부모 `1b45ed57` |
| 병합 직전 HEAD | `47d84eeb`(커밋 2개: `96080873` 본 구현, `47d84eeb` Windows 시험 수정) |
| 정합성 | squash 커밋의 tree가 승인 HEAD의 tree와 같다(diff 0). 파일 9개, +1,996/−35. 범위 밖 파일 없음 |
| PR CI (`47d84eeb`) | 필수 5개 SUCCESS. Safety invariants·Classify SUCCESS. Deferred·Full·Legacy는 scope 분류로 skip(`deferred/full/legacy: false`) |
| main CI (`8979e702`) | 필수 5개 SUCCESS. Windows 10,712 passed / 294 skipped(상한과 같음), Ubuntu 11,006 passed / 0 skipped. CPython 3.12.10(Windows)·3.12.15(Ubuntu) |
| 첫 HEAD의 실패 | `96080873`에서 Windows 9건 실패: Windows에서도 도는 시험 2개가 `os.getuid()`를 썼다. 합성 uid `"1"`로 고쳤다. skip은 추가하지 않았다 |
| 운영 | 배포 없음. compose·계정·소켓·로그 경로 없음. **운영 적용 HOLD, Claude read-bridge 접근 BLOCKED** |

**main의 코드와 이 worktree의 코드는 같지 않다.** (2026-10-16 PR-B 병합 시점의 기록이다. PR-C와 PR-A도 이 원칙대로 main에서 새 브랜치를 만들었다.)
- main의 PR-B 파일은 D안 주석이다. 설계 문서 § 대신 PR-B의 history fragment `docs/history/2026-10-16-claude-read-door-profile.md`를 가리킨다.
- 이 worktree(`/root/wt-option-c`)의 같은 파일은 그 전 주석 판이다. 런타임 코드 4개는 동작이 같다(AST 비교). 새 시험 파일 하나는 main 쪽이 Windows 수정(합성 uid)을 담고 있다.
- 그래서 PR-A·PR-C 브랜치는 main에서 새로 만들고 자기 파일만 담는다. 이 worktree의 PR-B 파일은 쓰지 않는다.

#### 4.22.2 PR-C 판단 자료 (2026-10-16 Thomas 결정됨 → §4.23)

PR-C의 base는 이제 `8979e702`다. PR-B는 `scheduler.py`와 factory 자식 시험을 건드리지 않았으므로 충돌하지 않는다(파일이 겹치지 않음).

**1. crypto 연구 중단 예외("bug fixes") 해당 여부**
- **바뀌는 범위:** `scheduler.py`(코어)뿐이고 `crypto/`는 바뀌지 않는다. factory 자식 실패 기록의 노출 결함(예외 메시지가 원장·알림·door로 흐름)을 고친다.
- **판단 입력(정적 확인):** factory 실패 메시지 부분을 판단 입력으로 쓰는 코드가 없다.
  - crypto의 `last_status` 독자(data_review, cycle, dashboard)는 자기 스케줄의 성공 요약만 읽는다.
  - 알림 전환은 바뀌지 않는 `failed:` 접두어만 본다.
  - template·hypothesis·trial·예산과 관계없다.
- **권고:** 해당한다.
- **예외를 승인해도 허용되지 않는 것:** 생성과 병합은 따로 승인받는다. 배포는 별도이고 지금 HOLD다.

**2. E-01 운영자 진단 열람 경로(위험도 MEDIUM)**

e2가 배포되면 알림·`last_status`·원장에는 예외 타입만 남는다. 메시지는 `.runtime_governance_state/diagnostics/factory_child_errors.jsonl`에만 남는다.

| 대안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| E1-a | Thomas가 호스트(root, SSH)에서 그 파일을 읽는다 | 코드 없음, 지금 가능 | 손으로 하는 절차. 어디를 볼지 알아야 한다(문서화 필요) |
| E1-b | `scheduler_cli`에 운영자 전용 읽기 명령(예: 진단 tail)을 추가한다. Thomas가 `docker exec thomas-scheduler python -m …`로 실행 | 재현 가능하고 PR-C 안에서 끝남 | 코드가 늘어남. door·hermes·Claude 경로로 노출하지 않는다는 조건이 필요 |
| E1-c | 알림에 메시지를 다시 붙인다 | 편함 | **e-2와 충돌.** 원문이 호스트 밖(Telegram)으로 나간다. 권고하지 않는다 |
| E1-d | 아무것도 하지 않는다 | — | 실패 원인이 사실상 보이지 않는다 |

- **권고:** 당장은 E1-a(PR-C 문서에 위치와 절차를 적는다). E1-b는 별도 후속으로 결정한다.
- **조건:** 어떤 경우에도 Claude에게 Docker·root를 주지 않고, 어떤 read door도 이 파일을 서비스하지 않는다.
- **E-02–E-04:** 크기가 작아 PR-C에 함께 넣을 수 있다. 넣을지는 PR-C 범위 결정이다.
  - E-02: `except Exception`, 또는 진단 기록을 terminal event 뒤로
  - E-03: 콜론이 없을 때는 상수만 허용
  - E-04: 0600/0700

**3. PR-C 승인 단계(서로 독립)**
1. e2 항목별 확정
2. crypto 예외 판단
3. E-01 방식, E-02–E-04 포함 여부
4. PR-C 생성 승인
5. PR-C 병합 승인

배포는 그 뒤 별도다.

#### 4.22.3 PR-A(이 문서) 상태

- 이 절은 2026-10-16(1) 시점의 기록이다. 최신 상태는 §4.24.
- **PR-A를 만들 때:** main(`8979e702` 이후)에서 브랜치를 만들고 문서 파일만 담는다. STATUS를 다시 생성한다. PR-C의 실제 상태를 다시 반영한다.

### 4.23 PR-C 보안 수정 승인과 저장소 구현 (2026-10-16)

**근거:** Thomas 2026-10-16 명시 지시. 범위는 저장소 안 코드·문서·합성 시험이다. 커밋, push, PR 생성, 병합, 운영 배포는 승인되지 않았다.

#### 4.23.1 승인 기록

| 항목 | 상태 |
|---|---|
| D-11 e2 | **EXPLICITLY_APPROVED(저장소 내부 보안 구현만, repository implementation only)** |
| crypto 연구 중단 정책 예외 | **APPROVED(보안 버그 수정에 한정, security bug fixes only)**(§4.23.2) |
| E-01 | **E1-a APPROVED(문서화만)**(§4.23.3) |
| E-02 | **APPROVED(저장소 보안 수정만)** |
| E-03 | **APPROVED(저장소 보안 수정만)** |
| E-04 | **APPROVED(저장소 보안 수정만)** |

**바뀌지 않는 것:**
- D-1, D-3, D-11 b3: PROVISIONALLY_ACCEPTED
- PR-C, PR-A: 미생성(이 승인 시점. 그 뒤 PR-C #1224 MERGED, §4.24)
- 운영 권한: 없음

#### 4.23.2 crypto 예외의 범위

**허용:**
- factory 자식 실패 메시지 노출 방지
- 실패 status 정규화
- 진단 기록의 안정성 개선
- 비정상 실패 문자열 검증
- 진단 파일 권한 강화
- 위 항목들의 회귀 시험과 합성 결함 주입

**불허:**
- 전략 변경, ResearchSignal 변경, 진입 방향·판단 변경, SL/TP 변경, PreOrderRiskGate 변경, 실행 단계 변경
- 실주문·testnet 주문 제출, live canary 활성화
- 거래 승인 정책 변경, 실제 운영 설정 변경
- 실계좌·잔고·포지션 접근

**Crypto_AI_System 상태:** review-only / signed-testnet-preparation 그대로다. 변경 파일은 `runtime/mvp_runtime/scheduler.py`와 그 시험뿐이다. `crypto/` 아래는 바뀌지 않았다.

#### 4.23.3 E-01 — 운영자 전용 진단 열람 절차 (E1-a, 문서만)

1. **목적:** factory 자식이 실패했을 때 그 원인(예외 메시지)을 운영자가 진단하기 위한 기록이다.
2. **담길 수 있는 정보:** 예외 메시지 앞 2000자. 파일 경로, 입력값, 심볼, 데이터 조각이 들어갈 수 있다. Secret이 섞였을 가능성을 배제할 수 없으므로 **Secret에 준해 다룬다.**
3. **운영자 전용 열람:**
   - Thomas만, 따로 승인된 운영자 호스트 접근 경로로 연다.
   - 내용은 필요한 만큼만 보고 다른 곳에 복사하지 않는다.
   - 대화·이슈·PR·로그에 붙이지 않는다.
4. **Claude 접근 금지:**
   - Claude는 이 파일을 읽지 않는다.
   - 이 파일을 읽게 하려고 Claude에게 Docker·root를 주지 않는다.
   - Thomas의 터미널을 Claude의 대리 실행기로 쓰지 않는다(기존 규칙).
5. **Hermes·read-bridge로의 노출 금지:**
   - 어떤 door의 read도 이 파일을 서비스하지 않는다. hermes door의 15개 read에도, Claude 프로필의 2개 read에도 없다.
   - 다만 f2의 `:ro` 마운트가 운영에 들어가면, Claude door 프로세스는 이 파일에 파일시스템으로 닿을 수 있다. 보장은 "어떤 read도 노출하지 않음"이다(§4.17.1 e-1 정정).
6. **Telegram 알림:** 예외 타입만 담는다(e-2). 원문을 다시 붙이는 E1-c는 금지다.
7. **최소 권한:**
   - 파일 0600, 디렉터리 0700. scheduler 서비스 uid가 소유한다.
   - 열 수 없거나 private이 아니면 기록하지 않는다. 그때 서비스 로그에는 고정 코드 `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE`만 남는다.
8. **보존·회전:** 미정이다. 지금은 계속 덧붙이기만 한다. R-08과 함께 운영 정책으로 정해야 한다.
9. **운영자가 확인할 수 없을 때:**
   - status·알림의 예외 타입과 `schedule_run_id`, 서비스 로그의 `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE` 행으로 해당 실행(occurrence)을 특정한다.
   - 원문이 필요하면 따로 승인된 절차로 재현하거나 E1-b(운영자 전용 CLI)를 결정한다.
   - 원문을 Claude에게 넘기지 않는다.
10. **운영 적용:** e2의 배포와 실제 파일 경로 생성은 별도 승인이다. 이 문서는 열람을 실행할 권한을 주지 않는다. 이번 작업에서 실제 파일을 열거나 호스트 명령을 실행하지 않았다.

#### 4.23.4 E-02–E-04 구현 (`runtime/mvp_runtime/scheduler.py`)

- **E-03 실패 문자열 정규화(`factory_child_failure_status`):**
  - 자식이 쓰는 `"<type>: <message>"` 형식에서, 첫 `": "` 앞부분이 ASCII 클래스 이름(`^[A-Za-z][A-Za-z0-9_]{0,63}\Z`)일 때만 타입으로 쓴다.
  - 나머지는 모두 `failed:FACTORY_CHILD:UNKNOWN`이다: 구분자가 없는 문자열, 빈 문자열, 비문자열, 줄바꿈, 제어 문자, 비ASCII, 짝 없는 surrogate, 65자 이상, 경로 모양.
  - 예외는 부모가 직접 쓰는 `CORRUPT_RESULT` 하나(상수 `FACTORY_CHILD_CORRUPT_RESULT`)다.
  - `failed:` 접두어는 유지된다.
  - UNKNOWN을 새 코드 대신 셋째 칸에 둔 이유: 기존 계약 `failed:FACTORY_CHILD:<Type|UNKNOWN>`와 투영 v1의 `failed:FACTORY_CHILD` 정규화가 그대로 맞는다.
- **E-02 진단 기록의 예외 처리(`_record_factory_child_error`):**
  - 넓은 catch는 이 함수 안에만 있다.
  - 실패하면 False를 돌려주고, 서비스 로그에 `FACTORY_CHILD_DIAGNOSTICS_UNAVAILABLE schedule_id=… schedule_run_id=…`를 남긴다(메시지는 없음).
  - occurrence의 status는 그대로 failed이고 terminal event도 그대로 기록된다.
  - 행은 `ensure_ascii` JSON이라, 짝 없는 surrogate나 제어 문자가 있어도 인코딩 오류가 나지 않는다.
  - 덜 써진 행은 시작 위치로 잘라 되돌린다.
- **E-04 최소 권한(`_append_diagnostics_row`, `_open_private`):**
  - 디렉터리가 없을 때만 0700으로 만든다. 이미 있는 디렉터리는 chmod하지 않는다.
  - 디렉터리는 `O_DIRECTORY|O_NOFOLLOW`로 연다. 파일은 그 디렉터리의 descriptor 기준(`dir_fd`)으로 `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, 0600으로 연다.
  - 열린 descriptor를 검사한다. 디렉터리: 디렉터리인지, 소유자, group/other 권한 없음. 파일: 일반 파일, 링크 수 1, 소유자, group/other 권한 없음. 하나라도 어긋나면 거부한다.
  - O_NOFOLLOW·O_DIRECTORY·dir_fd가 없는 플랫폼(Windows)에서는 거부한다(`factory_child_diagnostics_supported`).
  - 공통 `filelock`은 바꾸지 않았다. 쓰는 쪽이 maintenance lane 하나라 잠금이 필요 없다.
- **기존 동작:**
  - 성공 경로와 판단 로직은 바뀌지 않았다.
  - 고아 파일 정리, 기존 원장 형식과의 호환(과거 행은 그대로, 투영은 두 형식을 같게 정규화)도 그대로다.
  - `docs/DIAGNOSTIC_CODE_INDEX.md`는 다시 생성됐다. 메시지만 있는 raise 집계가 33→36이 됐는데, 함수 안에서 잡히는 OSError 3개다.

#### 4.23.5 시험과 결함 주입

- **시험 파일:** `tests/test_mvp_runtime_scheduler_factory_child.py`, 45건(이전 14건).
  - E-03 정규화 20개 사례
  - E-02 진단 실패 3종(OSError·UnicodeEncodeError·RuntimeError): terminal event 유지, 메시지 미노출, 고정 코드
  - 플랫폼 미지원 시 거부
  - E-04: 0600/0700 생성, 인코딩, 잘린 쓰기, 비private 9종(디렉터리 symlink, 디렉터리 group 권한, 디렉터리 소유자, 파일 symlink, hard link, 파일 group 권한, 파일 소유자, FIFO, reader가 있는 FIFO). 각 사례에서 원본 불변, chmod 없음을 확인
- **결함 주입(scratch, CI 밖):** 16종 모두 시험 실패를 일으킨다.
  - 처음에는 4종이 빠졌다. 주입 문자열 오류, 그리고 대상 파일이 다른 검사에 먼저 걸린 경우였다. fixture를 고쳐서 각 검사가 단독으로 걸리게 했다.
- **회귀:** scheduler, scheduler CLI, factory, crypto cycle·data review·forward cohort·pool, heartbeat, read-bridge 투영, domain isolation, 진단 인덱스: 814 passed.
- **Windows(모의: fork·O_NOFOLLOW·O_DIRECTORY·getuid 없음):** 33 passed, 12 skipped(POSIX 진단 시험), 0 failed. **실제 Windows는 NOT_VERIFIED.** PR-C를 만들면 win32 skip이 294→306(예상)이 되고, 상한은 그 PR의 실측으로 정한다.

#### 4.23.6 남은 위험과 PR-C 전 작업

**남은 위험:**
- 진단 파일 보존·회전 미정(R-08과 함께)
- 운영자 원문 열람이 수동 절차(E1-a)
- f2 `:ro`가 운영에 들어가면 Claude door 프로세스가 파일시스템으로 닿을 수 있음(read로는 노출 안 함)
- Windows 실측 없음

**PR-C 생성 전 작업** (완료: 2026-10-16 PR-C #1224를 main에서 만들어 병합. `0b6fd69c`):
1. main(`8979e702` 이후)에서 브랜치를 만들고 `scheduler.py`·시험만 담는다(이 worktree의 PR-B 파일은 쓰지 않음).
2. 코드 주석을 D안처럼 정리한다(설계 문서 § 대신 PR-C history 참조).
3. PR-C history fragment를 쓴다.
4. main 기준으로 `DIAGNOSTIC_CODE_INDEX.md`를 다시 생성한다.
5. win32 상한은 CI 실측으로 정한다(예상 306).
6. PR-C 생성 승인을 받는다.

### 4.24 PR-A: 병합 상태, 최종 결정 상태, 미해결 항목 (2026-10-16)

Option C의 저장소 구현은 PR-B·PR-C로 main에 들어갔다. 이 문서(PR-A)는 그 결정과 남은 일을 기록한다.

**운영 상태:** 배포는 없다. Option A의 DEPLOYMENT_HOLD 그대로다.

**§4.17–§4.23을 읽을 때 주의할 점:**
- 그 절의 worktree 경로(`/root/wt-option-c`), 되돌리기 명령, "커밋 없음"은 각 날짜 시점의 기록이다.
- 지금의 코드는 아래 병합 커밋의 main이다.

#### 4.24.1 병합 기록

| PR | 병합 | squash SHA | 부모 | 승인 HEAD | 검증 |
|---|---|---|---|---|---|
| PR-B #1223 "Option C: Harden Claude read-bridge boundary" | 2026-10-16T03:12:35Z, squash | `8979e702` | `1b45ed57` | `47d84eeb` | squash tree = 승인 HEAD tree. 9개 파일, +1,996/−35. PR CI·main CI 필수 5개 SUCCESS. win32 10,712 passed / 294 skipped(상한과 같음). linux 11,006 passed / 0 skipped. CPython 3.12 |
| PR-C #1224 "Option C: Scheduler e2 factory child failure hardening" | 2026-10-16T04:32:28Z, squash | `0b6fd69c` | `8979e702` | `c5e641fe` | squash tree = 승인 HEAD tree. 5개 파일, +449/−5. PR CI·main CI 필수 5개 SUCCESS. win32 10,738 passed / 306 skipped(상한과 같음). linux 11,044 passed / 0 skipped. CPython 3.12 |

- **win32 skip 상한 변화:** 258 → 294(PR-B +36) → 306(PR-C +12). 모두 CI 실측과 같다.
- **skip 사유:** AF_UNIX door 22건, POSIX 요청 로그 14건, POSIX 진단 파일 12건.
- **이 플랫폼 제한의 의미:** Windows에서는 해당 기능이 거부된다. 그 거부 자체는 Windows에서도 시험된다.

#### 4.24.2 최종 결정 상태

| 항목 | 상태 | 저장소 반영 |
|---|---|---|
| Option A(2026-10-12) | EXPLICITLY_APPROVED | 문서(readiness §4.5) |
| D-1 대안 C | PROVISIONALLY_ACCEPTED | 없음(실행기 미구현) |
| D-3 §4.3 범위 Reuse-first 예외 | PROVISIONALLY_ACCEPTED | 없음 |
| D-11 a1(코드 부분)·c1·d2·f2 | EXPLICITLY_APPROVED(저장소 구현 범위, 2026-10-16) | main `8979e702` |
| D-11 b3 | PROVISIONALLY_ACCEPTED | 없음(지금 b1, 정책 변경 없음) |
| D-11 e2(e-1–e-3) | EXPLICITLY_APPROVED(저장소 내부 보안 구현, 2026-10-16) | main `0b6fd69c` |
| crypto 연구 중단 예외 | APPROVED(보안 버그 수정만, 2026-10-16) | PR-C에만 적용. `crypto/` 변경 없음 |
| E-01 E1-a | **APPROVED — 문서만.** 운영자 전용 열람 절차를 정의했다(§4.23.3) | 코드 없음. 실제 열람 실행과 E1-b CLI는 미승인 |
| E-02·E-03·E-04 | **APPROVED(저장소 보안 수정) — 코드 병합 완료** | main `0b6fd69c`(시험 45건, CI 통과) |
| OPERATIONALLY_AUTHORIZED | **없음** | — |

- **E-01과 E-02–E-04의 구분:** E-01은 절차 문서다. 운영자가 그 절차를 실제로 실행하는 것은 별도 운영 승인이다. E-02–E-04는 main에 병합된 코드이고, 운영에 배포되지 않았다.
- **PR 상태와 상태 용어:** PR 상태(MERGED)와 위 상태 용어는 별개다. 병합은 운영 권한이 아니다.
- **원칙:**
  - `CODE_MERGED != OPERATION_APPROVED`: 코드가 main에 있다고 운영에 쓸 수 있는 것이 아니다.
  - `CI_PASS != DEPLOYMENT_APPROVED`: CI 통과는 배포 승인이 아니다.
  - 문서에 기록했다고 해서 미승인 운영 결정이 확정되지 않는다.
- **Option A(2026-10-12)는 그대로다:**
  - Preflight Secret 처리 3-2 REJECT, Deploy/Rollback Secret 처리 3-1 REJECT
  - B′ 채택하지 않음, Rollback 비상 권한 NOT_GRANTED
  - DEPLOYMENT_HOLD, NO_APPROVED_EXECUTION_PATH
  - ROLLBACK_FUNCTIONAL_VERIFIED 부여하지 않음

  근거는 `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md` §4.5와 이 문서 §2다.
- **Claude read-bridge:** **BLOCKED**.
  - 프로필 허용 read는 `schedules`, `scheduler_events` 2개다.
  - 보류 read는 heartbeat, runtime_status, crypto_readiness, holdings_status 4개다. 운영에서 활성화하지 않는다.

#### 4.24.3 미해결 항목 (운영·설계, 모두 미승인)

| 영역 | 남은 것 | 근거 절 |
|---|---|---|
| 실제 OS 권한 분리 | 호스트에 적용하지 않았다(C5·C6를 실행한 적 없음). Claude 세션 계정 분리, docker 그룹 분리, Guard·hook 관리 설정(D-13), C5·C6 단계, 부정 시험 N-1–N-6 모두 미실행 | §4.6, §6, §8 |
| Claude read door 운영화 | 계정·uid/gid, 소켓 경로·디렉터리 권한, 요청 로그 디렉터리, compose a1 서비스와 `:ro` 마운트 모두 미결정·미구현. 운영 검증 O-1–O-9는 실행하지 않았다(NOT_TESTED). 운영 preflight와 실제 배포 검증도 실행하지 않았다. **접근은 BLOCKED** | §4.17.5, §4.16.3 |
| R-08 보존·회전 | 요청 로그와 진단 파일 모두 보존 기간·회전 방식·디스크 가득 참 대응이 미정. 지금은 계속 덧붙이기만 한다 | §4.19.5, §4.23.3 |
| 진단 파일 격리 | **요구사항: 진단 파일은 Claude 실행 환경(Claude door 프로세스 포함)에 마운트되지 않도록 격리해야 한다.** 운영 경로 분리는 별도 승인 전에는 구현하지 않는다. 코드는 0600/0700, no-follow, descriptor 검사를 한다. 다만 운영 경로의 실제 권한은 검증되지 않았다. f2 `:ro`를 운영에 적용하면 Claude door 프로세스가 상태 루트 안의 진단 파일에 파일시스템으로 닿을 수 있다(read로는 노출되지 않음). 진단 파일을 상태 루트 밖으로 옮길지 결정이 필요하다. 백업에 포함되는지 NOT_VERIFIED | §4.23.3, §4.23.6 |
| E-01 실행 | E1-a 절차를 실제로 쓰는 것은 운영 승인 사항이다. E1-b(운영자 전용 CLI)는 미결정이다 | §4.23.3 |
| 배포 | PR-B·PR-C 모두 운영 미배포. e2를 배포하면 운영 scheduler의 기록 형식이 바뀐다(원장에 두 형식이 섞임). 배포는 별도 승인이다 | §4.22.1 |
| 추가 read | heartbeat·runtime_status 프로필 등록(R3-6), crypto_readiness 기록 기반 builder(R3-3), holdings_status 계약(R3-4) | §4.20.7 |
| 실행기 | D-1 구현, D-2 승인 채널, D-4 `inspect_candidate`, D-10 실행 계정, D-5–D-9·D-12–D-16 | §8 |
| 응답 없이 끝난 연결 | 감사 공백(timeout·oversize·hang-up)이 합의된 상태로 남아 있다 | §4.19.4 |

#### 4.24.4 PR-A의 범위

- **파일:** 이 설계 문서, `HOLDINGS_C7_PR_D_DEPLOY_READINESS_V0.1.md`(Option A 결정 기록), `docs/history/2026-10-12-host-privilege-separation.md`, `docs/proposals/STATUS.md`(생성 문서). 코드와 시험은 없다.
- **승인:** 생성과 병합은 각각 Thomas의 별도 승인이 필요하다.
- **잠정 결정:** 병합해도 잠정 결정(D-1, D-3, D-11 b3)은 확정되지 않는다.

### 4.25 C2 완료와 C3a 구현 진행 (2026-10-16)

#### 4.25.1 결정 기록

| 결정 | 승인 문구(원문) | 범위 |
|---|---|---|
| C2 착수 | D-1 대안 C·D-2 R2·D-3(C2 명세 범위)·D-4·CR-1–CR-5 명시 승인, D-10 보류 | DESIGN_ONLY |
| N1 | N1-1–N1-8 명시 승인(G/X 분리, 최종 판정은 X에만) | DESIGN_ONLY. 원문은 명세 머리말 |
| GAP | `승인: GAP-1A~1C, GAP-2A~2C, C2-COMPLETE DESIGN_ONLY (Thomas 2026-10-16)` | DESIGN_ONLY |
| C2-FINAL | `승인: C2-FINAL C2 설계 완료 확정, DESIGN_ONLY (Thomas 2026-10-16)` | 명세 v0.5(md5 `5d096745`) 확정 |
| D-10a | `승인: D-10a G·X 비root 전용 계정, X Docker root 동등 위험 유지, DESIGN_ONLY (Thomas 2026-10-16)` | D-10b(`.env` 접근)는 미결정 |
| CR-12 | `승인: CR-12 T3 설치본 앵커 조건부 채택(Docker argv 조건 1–3, X 침해 NOT_PROTECTED), DESIGN_ONLY (Thomas 2026-10-16)` | 조건 1–3은 C3a-3 시험이 고정 |
| TYPE-B-KEY | `승인: TYPE-B-KEY K1 같은 키+namespace 제한, C5 전 K2 재검토, DESIGN_ONLY (Thomas 2026-10-16)` | — |
| D-3 | `승인: D-3 C3a 범위(Secret 경로 없음, validate·promote·rollback 미구현), DESIGN_ONLY — 구현 승인 아님 (Thomas 2026-10-16)` | — |
| CR-8 | `승인: CR-8 8a/8b 분리, CR-10·11·19 불변식만 C3·값은 C4 측정 후, DESIGN_ONLY (Thomas 2026-10-16)` | — |
| 사후 추인 | `추인: PR #1229·#1230·#1231·#1232의 기존 산출물 보존 및 승인된 수정 작업 허용. 과거에 사전 승인이 있었던 것으로 소급 기록하지 않음. 병합·배포 제외 (Thomas 2026-10-17)` | 네 PR은 2026-10-16 8시간 자율 위임 안에서 만들어졌다. 그 위임은 C3 구현과 문서 PR(CR-16)에 필요한 별도 명시 승인을 대신하지 않는다 |
| C3a-3 계약 정합·C2 최소 개정 | `승인: #1229 계약 일치 수정 및 PR 갱신. 항목 1·3a·3b·5는 C2 명세 우선. 3c는 요청 상태 enum 정의. 항목 2는 COMPOSE_PROJECT_MISMATCH 신설을 위한 최소 C2 계약 개정. 항목 4는 FAILED_EFFECT_PARTIAL에 승인된 비밀정보가 아닌 측정값만 포함하도록 응답 스키마 수정. 병합 제외 (Thomas 2026-10-17)` | C2 개정은 reply code 하나뿐이다(§4.25.3). 명세 원문은 바꾸지 않았다 |
| 문서 정합 | `승인: #1230·#1232의 계약 정합성 문서 수정 및 PR 갱신. 병합 제외 (Thomas 2026-10-17)` | — |
| C3a-3 병합 | `승인: C3a-3 #1229 squash 병합(head a5729653, base fa77912e; 설치·운영 제외) (Thomas 2026-10-17)` | main `2cf78cac` |
| C3b 구현 | `승인: C3b 서비스 진입점·설정 로더·설치 도구·systemd unit 템플릿 구현 및 PR 생성(합성 시험만; 설치·실행·CI 변경 제외) (Thomas 2026-10-17)` | §4.25.4 |
| C3b 병합 | `승인: C3b #1234 squash 병합(head cf0cfae4, base d2e853bb; 설치·실행 제외) (Thomas 2026-10-17)` | main `c88708de` |
| C4 계획 병합 | `승인: C4 계획 #1232 squash 병합(head b03a4905, base c88708de; 문서만) (Thomas 2026-10-17)` | main `66e24fde` |
| CR-8b·D-8 | `결정: CR-8b Python 3.12.13, 인터프리터 출처 python-build-standalone x86_64(SHA256 설치 기록 고정, 없으면 재보고); D-8 C4 환경 GitHub Actions 임시 러너(무료 한도 안, 비필수 workflow — CI 변경은 별도 승인) (Thomas 2026-10-17)` | §4.25.4 |
| C4 workflow | `승인: C4 시험 workflow 구현 및 PR 생성(GitHub Actions 임시 러너, 비필수, Python 3.12.13 PBS SHA256 고정, N-1~N-14·누출 검사; 병합·운영 제외) (Thomas 2026-10-17)` | 위 D-8의 "CI 변경은 별도 승인"에 해당한다 |
| CLOCK-SOURCE | `결정: CLOCK-SOURCE = A(커널 adjtimex, STA_UNSYNC 해제 + maxerror ≤ 16초), DESIGN_ONLY (Thomas 2026-10-17)` ("권고안대로 승인") | 실행기는 커널의 NTP 상태만 믿는다. chrony·timesyncd 어느 쪽이든 같다. 이 호스트는 chrony이고 timesyncd 표지 파일이 없어 그 안(C)은 제외했다 |
| CLOCK-SOURCE 구현 | `승인: CLOCK-SOURCE A 구현(C3 코드 PR, serve 연결·합성 시험) 및 C4 하네스 확장(합성 트리·이미지·8개 컨테이너, N-10·N-11·N-12 재시험) PR 생성(병합·운영 설치 제외) (Thomas 2026-10-17)` | `deploy_runner/clock_source.py`. CR-10·11·19 값은 여전히 미정이다 |
| BUILD-HOME | `결정: BUILD-HOME = A(실행기 명령 환경에 DOCKER_CONFIG=<X 기록 디렉터리>/docker-config 추가, 값은 root 소유 설정에서; DOCKER_HOST 등 그 밖의 DOCKER_*는 계속 차단), git safe.directory는 C5 설치 절차에 포함, DESIGN_ONLY (Thomas 2026-10-17)` | C4에서 X의 `docker image build`가 홈이 없어 실패했다(`mkdir /nonexistent`). 명령 환경 불변식은 "`PATH`·`LANG`, 그리고 `DOCKER_CONFIG` 하나"로 바뀐다 |
| C5 준비 | `승인: C5 준비 — 설치 도구에 .pth·git safe.directory·docker-config 처리 추가, C4 workflow가 설치 도구를 쓰도록 변경, PR 생성(병합·운영 설치 제외) (Thomas 2026-10-17)` | 설치 도구에 `link-install`·`trust-tree`를 더하고, `genesis`가 `docker-config`도 만든다. 운영 설치(C5 본작업)는 별도 승인이다 |
| CR-10·11·19 | `결정: CR-10 max_lifetime 900초, CR-19 prepare_candidate 마감 300초(validate·promote·rollback은 구현 시 재결정, 임시 300초), CR-11 issue_skew 60초·clock_jump 5초·backstep 5초, DESIGN_ONLY — C5 설치 설정에 적용, 운영 호스트 첫 설치 시험에서 재확인 (Thomas 2026-10-19)` ("권고안대로 승인") | 근거: #1242의 러너 측정(실제 Dockerfile 빌드는 캐시 없이 14.5초·캐시 있을 때 1.7초, G→X 왕복 중앙값 138ms·최대 1.1초, 커널 maxerror 3.5ms). 수명 15분은 거버넌스 정책 `RELEASE_DEPLOYMENT: 15`와 같다. "남은 수명 ≥ 마감" 규칙 때문에 서명 뒤 요청까지 10분 창이 생긴다. 값은 root 소유 설치 설정에 들어가고 코드는 바뀌지 않는다. 운영 호스트 빌드 시간은 재지 않았다 |

**정해지지 않은 것:** X 침해 위험의 운영상 수용, D-10b, CR-13, CR-15, CR-17(RECOVERY_INBOX는 우선 후보일 뿐), N2, D-5–D-9, D-12–D-16.

#### 4.25.2 C3a 구현

| 단계 | 내용 | PR | 상태 |
|---|---|---|---|
| C3a-1 | 승인 검증, 닫힌 reason code와 고정 문구, 신뢰 파일(T3), 시간 규칙 | #1227 | MERGED `8358369d` |
| C3a-2 | 소비 저장소, 요청 상태, 감사 체인, LATCH, 시작 대조 | #1228 | MERGED `4680754f` |
| C3a-3 | G·X와 두 소켓, `inspect_candidate`·`prepare_candidate`, 닫힌 Docker argv | #1229 | MERGED `2cf78cac`(2026-10-17, 계약 정합 수정 반영) |
| C3b | 서비스 진입점(`serve.py`), root 소유 닫힌 설정(`deploy_runner_config.v0.1`), 설치 도구, systemd unit 템플릿 | #1234 | MERGED `c88708de`(2026-10-17) |

- 각 단계의 근거와 수치는 `docs/history/2026-10-16-deploy-runner-c3a-*.md`와 `docs/history/2026-10-17-deploy-runner-c3b-service-entry.md`에 있다.
- **승인 근거:** C3a-1·C3a-2는 구현 전에 한 줄 승인이 있었다. C3a-3에는 없었다. C3a-3는 8시간 자율 위임 안에서 만들어졌고, 2026-10-17에 사후 추인됐다. 추인은 사전 승인으로 기록하지 않는다.
- 코드는 어떤 서비스에도 연결되지 않았고 설치되지 않았다. 운영 상태는 §4.24와 같다: DEPLOYMENT_HOLD, NO_APPROVED_EXECUTION_PATH, OPTION_C_EXECUTOR는 저장소 코드만 있고 미설치.

#### 4.25.3 C3a-3와 명세의 정합 (2026-10-17 수정 반영)

2026-10-17 검토에서 #1229 첫 판이 닫힌 계약과 다른 곳 다섯을 찾았다. Thomas가 명세 우선으로 수정을 승인했고, 수정이 반영됐다.

| 항목 | 명세 | 첫 판 | 수정 뒤 |
|---|---|---|---|
| X 소켓 peer 거부 | §6.1b·N1X-03: `ACTOR_PEER_NOT_PERMITTED` | `PEER_NOT_PERMITTED` | 명세대로. G 소켓은 `PEER_NOT_PERMITTED`. 소켓마다 자기 응답 형식을 쓴다 |
| compose 라벨 불일치 | §6.3-7은 대조를 요구하지만 code가 없었다 | `RUNNER_ENVIRONMENT_MISMATCH`(§9의 시작 지문 전용 code)를 빌려 씀 | **C2 최소 개정(Thomas 2026-10-17):** 응답 code `COMPOSE_PROJECT_MISMATCH` 하나를 새로 둔다. code 표에서 나오는 C3a-1 응답·감사 enum과 C3a-2 LATCH enum에 추가됐다 |
| 요청·응답 필드 | §6.1a: `approval_document`. §6.1b: `thomas.actor_request.v1` `{schema, request_id, verb, args, approval_document_b64?, approval_signature?, gate_intake_seq}`, `thomas.actor_reply.v1` `{ok, reason_code, request_id, state, data, replayed}` | `approval_document_b64`, `proto`·`client_id`, `reason`·`error_ref`, `state` 없음 | 명세대로. X 감사의 `client_id`는 null이고, 귀속은 같은 `gate_intake_seq`의 G 접수 기록에 있다(GAP-2) |
| `state` 값 | §6.1b에 값 정의가 없었다 | — | **정의(2026-10-17):** X 요청 기록의 상태(`claimed`, `completed`, `refused_before_consume`, `unknown`, `partial`). 기록이 없으면(inspect, claim 전 거부) null. G는 호출자에게 넘기지 않는다(§6.1a에 없음) |
| 부분 실패 응답 | §5.2·§6.3: `FAILED_EFFECT_PARTIAL` 응답 `data`에 측정 id만 | 응답은 code만. C3a-1 응답 스키마가 거부 응답의 `data`를 금지했다 | 명세대로. `{pr, git_commit_sha, rollback_image_id, candidate_image_id}`, 값은 검증된 형식이거나 null. C3a-1 `deploy_runner_reply.v0.1`은 이 닫힌 모양을 `FAILED_EFFECT_PARTIAL`에만 허용한다 |
| inspect 상태 enum | §6.2: 6개 값 | `absent`·`removing` 추가 | 명세대로. 다른 값은 `IMAGE_STATE_UNAVAILABLE`. 없는 컨테이너는 `running`에서 null이고 `containers`에는 넣지 않는다 |

**명세와 아직 다른 것(결정 대기, 바뀌지 않음):**
| 항목 | 명세 | 구현 | 이유 |
|---|---|---|---|
| `environment_file` 라벨 | §6.3-7 | 대조하지 않음 | 비교할 설치 시점 기대값이 없다(C4) |
| 자기 트리 | §4.3: GitHub에서 직접 fetch | 트리가 미리 있어야 한다. `ORIGIN_FETCH_FAILED`는 발생 지점이 없다 | fetch는 새 network 능력이라 별도 결정이 필요하다 |
| 시계 동기 | CR-10·11·19 값은 C4 측정 후 | 동기 확인 출처가 없으면 `prepare_candidate`는 `CLOCK_UNTRUSTED` | 불변식만 C3에 둔다(CR-8) |
| `FRAME_TOO_LARGE` | 응답 code | 공용 전송이 너무 큰 프레임을 응답 없이 닫는다. 발생 지점이 없다 | §4.19.4의 "응답 없이 끝난 연결"과 같은 공백 |

- **빌드와 네트워크:** `--pull=false`는 이미 있는 base 이미지만 새로 받지 않게 한다. 없는 base 이미지는 받고, Dockerfile의 `apt-get` 단계는 빌드 중에 네트워크를 쓴다. 이 효과는 §6.3이 받아들인 것이고, 요청 값이 바꿀 수 없다.

#### 4.25.4 C3b 병합과 C4 준비 (2026-10-17)

- **C3b(#1234):** C4 계획 §2 P-3의 서비스 진입점이다. G·X는 `python -I -m runtime.mvp_runtime.deploy_runner.serve --role gate|actor --config …`로 시작하고, root 소유 닫힌 설정을 읽는다. 설정에는 실행 환경 지문(Python 버전과 배포 패키지 해시)이 있어 다르면 `RUNNER_ENVIRONMENT_MISMATCH`로 시작을 거부한다(C2X-12). 설치 도구(`scripts/ops/deploy_runner_install.py`)는 지문 계산, 소비 저장소 genesis, unit 렌더링, 설정 검사를 한다. 아무 호스트에도 설치되지 않았다.
- **CR-8b:** Python 3.12.13, 출처 python-build-standalone x86_64. 2026-10-17 공개 릴리스 목록에서 `cpython-3.12.13+20260807-x86_64-unknown-linux-gnu-install_only.tar.gz`가 있음을 확인했다(3.12.13의 최신 빌드 릴리스 `20260807`). 그 뒤 3.12.14·3.12.15 빌드도 있다. 결정은 3.12.13이고, 운영 이미지의 Python도 3.12.13이다. SHA256은 C4 workflow가 고정한다.
- **D-8:** C4는 GitHub Actions 임시 러너에서 한다. 러너는 작업마다 새로 만들어지고 끝나면 버려지는 VM이라 C4 계획 §3의 V1(별도 커널)에 가깝다. 운영 저장소의 Secret을 쓰지 않는다. 호스팅 러너에서 systemd unit을 실제로 띄울 수 있는지는 NOT_VERIFIED이고, workflow가 첫 실행에서 확인한다.
- **아직 없는 것:** RUNNER-FETCH 결정, CR-10·11·19 값, 운영 설치(C5·C6). CLOCK-SOURCE는 2026-10-17에 A로 정해졌다(§4.25.1).
- **C4 결과(2026-10-17, #1237 병합 `2766c26b`):** 임시 러너에서 FAIL 0, PASS 94, BLOCKED 3(CLOCK-SOURCE), NOT_TESTED 1, 누출 0건이다. C3b unit은 `-I` 아래에서 `.pth` 없이 import하지 못한다는 것이 드러났다. 세부는 C4 계획 §10에 있다. 운영 호스트 기준으로는 여전히 NOT_TESTED다.

## 5. 대안 비교

| 기준 | A 기존 구성요소만 | B 기존 도구 보강 + 제한 계정(sudo wrapper) | **C 최소 전용 실행기 (권고)** | D 별도 호스트·VM |
|---|---|---|---|---|
| Secret 격리 | 불가. 호스트에서 Docker를 호출하는 기존 서버가 없다. 결국 Claude가 Docker를 써야 함 | 계정 분리로 가능 | 계정 분리로 가능 | 가장 강함(물리 분리) |
| Docker 권한 격리 | 불가 | wrapper가 root로 돎. 인자 검증을 sudoers·셸로 해야 함 | 실행기만 docker 그룹, 닫힌 동작 | 같음 + 호스트 분리 |
| Claude의 코드 수정 가능성 | — | wrapper를 root 소유로 두면 불가. 단, wrapper가 Claude 트리를 빌드하면 가능 | 설치본·자기 트리로 불가 | 불가 |
| 승인 위조 방지 | — | wrapper가 승인을 검증해야 함(셸로 재구현) | R1·R2 재사용·구현 | 같음 |
| 운영 복잡성 | 낮음 | 중간(sudoers 관리) | 중간(서비스 하나) | 높음(호스트 하나 더, 네트워크) |
| 구현 비용 | — | 낮아 보이지만 검증·감사·멱등성을 셸로 다시 짬 | 중간. 전송·감사·판정·멱등성 재사용 | 높음 |
| 검증 가능성 | — | 셸 wrapper는 pytest로 덮기 어려움 | 기존 door 테스트 방식으로 합성 시험 | 같음 + 환경 시험 |
| 롤백 위험 | — | 낮음(sudoers 줄 삭제) | 낮음(서비스 중지, root로 기존 절차 복귀) | 중간(이전 비용) |
| 유지보수 | — | wrapper 수마다 증가 | 동작 목록 하나 | 호스트 둘 |

- A가 안 되는 이유: 기존 door는 컨테이너 안에서 돌고 Docker를 부를 수 없다(§2.3). 호스트 서버가 없으므로 Docker를 부르는 주체가 Claude로 남는다.
- B 대신 C를 권고하는 이유: 2026-10-09 안의 sudo wrapper도 경계가 되지만, 인자 검증·승인 결속·감사·멱등성을 셸로 다시 짜야 한다. C는 그것들을 Python 기존 구현에서 재사용하고 pytest로 시험한다.
- D는 C가 실패하거나 호스트 root 위험을 줄여야 할 때의 다음 단계로 남긴다.

## 6. 단계별 로드맵

한 Phase의 완료는 다음 Phase의 승인이 아니다. 각 Phase의 "별도 승인" 열은 그 Phase를 시작하기 전에 받는다.

| Phase | 선행 조건 | 범위 | 산출물 | 테스트 | 완료 기준 | 중단 조건 | 별도 승인 |
|---|---|---|---|---|---|---|---|
| **C0** 구조·위협 모델 확정 | 없음 | 이 문서 | 이 문서, readiness §4.5, history 기록 | `build_proposal_status.py --check`, 문서 lifecycle 테스트 | Thomas가 §8 D-1을 정함 | 이 문서의 사실이 소스와 다름 | 이 문서의 PR·병합 |
| **C1** 계정·권한 경계 설계 | C0 | Claude 작업 목록 전수, 대체 경로 표, read-bridge 대조, 자동화 영향, 승인 보안 가정 | §4.7–§4.11 (2026-10-13 작성) | 정적, 문서 테스트 | §4.11의 8개 기준. **문서 작업은 완료, cron 3줄·E 3건은 미확인** | 대체 경로가 없는 필수 작업 발견 → Thomas 판단(현재 없음) | cron 3줄·Mac SSH 계정·볼트 경로 증거(Thomas 제공 또는 확인 승인) |
| **C2** 승인 검증 인터페이스 | C0, D-2 | 선택한 채널의 결속 문서·reason code·소비 경로 | 인터페이스 명세 | 합성 승인으로 N-8–N-11 명세 | 승인 결속 필드와 거부 코드가 닫힘 | 선택한 채널이 결속 조건을 못 채움 | D-2 결정. 새 동작 유형·스키마의 Reuse-first 예외 |
| **C3** 실행기 구현과 합성 시험 | C2, D-3 | 실행기 코드(동작 목록, 응답 스키마, argv 실행, 감사). Docker는 가짜 `run`으로 | 코드 + 테스트 PR | N-7–N-12 합성, 누출 검사, 닫힌 동작 고정 테스트 | CI 5개 통과, 부정 시험 PASS | 합성 누출 1건이라도 발견 | 코드 PR·병합. **설치·활성화는 아님** |
| **C4** 비운영 환경에서 분리 검증 | C3 | 운영과 무관한 VM·컨테이너에 `claude`·`thomas-deploy` 계정과 가짜 `.env`를 만들어 N-1–N-14 실행(N-15는 D-13 뒤 C6) | 시험 기록 | N-1–N-14 | 모두 PASS | FAIL 1건 | 비운영 환경 사용(자원) |
| **C5** 운영 호스트 변경 계획 검토 | C4 | 계정 생성, 그룹, `.env` 모드·사본 처리, 실행기 설치, systemd, 앱 SSH 사용자, cron 이전, CLAUDE.md·skill 개정안 | 변경 계획서 + 되돌리기 절차 | 정적 | 각 변경의 되돌리기가 정해짐 | 되돌릴 수 없는 단계 발견 | 계획 승인 |
| **C6** 분리 실행 후 부정 시험 | C5 승인 | Thomas가 호스트 변경을 수행. Claude는 `claude` 계정에서 N-1–N-6, N-13–N-15 실행 | 시험 기록 | N-1–N-15(운영 호스트) | 모두 PASS, root Claude 프로세스 0 | FAIL → 즉시 root 경로 금지 유지, 원인 보고 | **호스트 계정·권한 변경(운영)**, 실행기 활성화 |
| **C7** 새 기준선 candidate 검증 계획 | C6 | 새 main SHA로 `prepare_candidate`·`validate_candidate` 계획 | 계획서 | — | 승인 대상 SHA·이미지가 정해짐 | 기준선 이동 | 계획 승인 |
| **C8** 운영 preflight와 배포 | C7 | 실행기 `validate_candidate` → `promote` | 배포 기록 | 사후 확인 | 사후 확인 통과 | STOP 1건 | **운영 preflight 승인, 배포 승인, Rollback 범위·기간** — 각각 따로 |

## 7. 배포 전제조건 체크리스트 (C8 진입 전)

- [ ] C6 부정 시험 N-1–N-15 모두 PASS (운영 호스트)
- [ ] root로 도는 Claude 프로세스 0 (`ps -eo user,comm`)
- [ ] 실행기 설치본 SHA = Thomas가 검토한 origin/main SHA
- [ ] 승인 채널(D-2) 운영 중, 결속·만료·소비 확인
- [ ] 새 candidate: SHA, candidate id, rollback id가 승인 문서에 결속됨
- [ ] V-5 상당 검증(C-7, D-10, 자가검증)을 새 candidate로 다시 수행. Q의 V-5 수용은 이전되지 않음
- [ ] Gate 6 조건 재확인: Rollback Target, Scheduler·Operator·Worker·Bridge 영향, hermes 영향, 진행 중 fire 중단 위험 인지, preflight STOP 0
- [ ] 운영 preflight 승인, 배포 승인, Rollback 비상 권한 범위·기간 — 각각 Thomas
- [ ] CLAUDE.md "Deploying"과 deploy·verify skill 개정이 병합됨(실행기 경로 반영)

## 8. 남은 의사결정

| # | 결정 | 권고 기본값 | 상태 |
|---|---|---|---|
| D-1 | 대안 선택(§5 A/B/C/D) | C | PROVISIONALLY_ACCEPTED(2026-10-13 권고 일괄 수용 지시의 해석, 항목별 확정 대기): C(§4.17). **2026-10-16:** 대안 C DESIGN_ONLY 승인(C2 착수 승인), N1-1로 유지(§4.25.1). 운영 권한은 없음 |
| D-2 | 승인 채널(§4.4 R1/R2/R3) | R2(SSH 서명). 승인 출처의 증명이 가장 강하고(서명은 root도 위조 못 함), 호스트에 이미 Thomas 공개키가 있다. R1은 Telegram 편의가 크지만 신뢰 기반이 root·uid 10001이 쓸 수 있는 호스트 파일이다. 어느 쪽도 root 침해에는 소용이 없다(§4.10) | **2026-10-16:** R2 DESIGN_ONLY 승인(C2 착수 승인). 키 유형은 TYPE-B-KEY K1(§4.25.1) |
| D-3 | 새 구성요소의 Reuse-first 예외: 실행기, 배포 동작 유형, 응답 스키마 | §4.3 범위로 한정해 승인 | PROVISIONALLY_ACCEPTED(2026-10-13 권고 일괄 수용 지시의 해석, 항목별 확정 대기): §4.3 범위(§4.17). **2026-10-16:** C3a 범위 DESIGN_ONLY 승인(Secret 경로 없음, validate·promote·rollback 미구현)(§4.25.1) |
| D-4 | `inspect_candidate`를 승인 없이 허용할지 | 허용(지정 메타데이터, env·Mounts 제외) | **2026-10-16:** DESIGN_APPROVED(C2 착수 승인). C3a-3에서 고정 필드·속도 제한으로 구현(#1229) |
| D-5 | Claude가 실행기 감사 기록을 읽을지 | 허용(메타데이터뿐) | 미결정 |
| D-6 | `.env` 백업 사본 3개 처리 | 호스트 밖 보관 후 삭제 | 미결정 |
| D-7 | `claude` 계정의 GitHub 자격 범위 | 현재와 같은 저장소 범위, `workflow` 권한 없음(현재와 같음) | 미결정 |
| D-8 | 비운영 검증 환경(C4) | 임시 VM 또는 rootless 컨테이너 안의 systemd | **2026-10-17:** GitHub Actions 임시 러너(무료 한도 안, 비필수 workflow). CI 변경은 별도 승인(§4.25.1, §4.25.4) |
| D-9 | 상태 CLI 대체 경로(C1) | 읽기는 read-bridge에 있는 것만, 쓰기는 Thomas | 미결정 |
| D-10 | 실행기가 `.env`·상태 파일에 닿는 방법(§4.2 E1/E2/E3) | E1(root 실행). docker 그룹이 이미 root와 같기 때문 | **2026-10-16:** D-10a DESIGN_ONLY 승인 — G·X 모두 비root 전용 계정, X의 Docker = root 동등 위험은 유지. `.env` 접근(D-10b)은 미결정(§4.25.1) |
| D-11 | Claude 계정이 read-bridge에 닿는 방법(§4.8 a–d, §4.12.1) | (d)(= §4.14 a1) Claude 전용 read-bridge 인스턴스: 소켓은 `bridge/` 밖, uid 허용 목록은 그 서비스의 리터럴, Claude는 다른 door에 닿지 않는다. 이전 권고 (a)는 허용 목록 변수가 네 door에 공유되므로 철회(2026-10-13). 승인 전 검증(§4.12.4) 결과로 붙는 조건: uid와 gid 둘 다 리터럴, `scheduler_events`의 허용 필드 투영(factory 실패 문구에 예외 메시지가 들어감), 요청 로그, `:ro`는 읽기 목록 축소 없이는 불가. 정책 actor는 따로 정한다. 승인 조건은 필수(a–f)·구현·운영 검증으로 나눴고, 투영(c) 없이는 Claude를 받지 않는다(§4.12.5). 결정 패킷: §4.14, 권고 조합 a1+b3+c1+d2+e2(별도)+f2. 투영 구현 명세는 §4.13 | PROVISIONALLY_ACCEPTED(2026-10-13 권고 일괄 수용 지시의 해석, 항목별 확정 대기): a1·b3·c1·d2·e2·f2(§4.17). 구현은 IMPLEMENTED_PENDING_REVIEW. f2는 2개만 구현(§4.18.3). 하위 값 대기(§4.17.5, §4.18.6) **2026-10-16:** a1(코드)·c1·d2·f2 EXPLICITLY_APPROVED(저장소 구현), PR-B MERGED(`8979e702`). b3는 PROVISIONALLY_ACCEPTED. e2는 2026-10-16 EXPLICITLY_APPROVED(저장소 보안 구현), PR-C MERGED(`0b6fd69c`). 운영 단계는 모두 미승인(§4.24.3). 운영 단계는 모두 미승인 |
| D-12 | `hermes-notify.sh`처럼 Secret을 읽는 Claude 디렉터리의 스크립트 | root 소유 위치로 옮기고 Mac 자동화 전용. 분리 전이라도 옮기는 것을 권고(§4.9) | 미결정 |
| D-13 | Guard·hook을 Claude가 쓸 수 없는 관리 설정에 둘지 | 둔다. 위치와 우선순위는 C5에서 확인 | 미결정 |
| D-14 | E 분류 3건(§4.7 W-11–W-13. W-30은 §4.12.3에서 D로 재분류. 표준 비교와 선택지는 §4.15.1–§4.15.2) | W-11·W-12: 소스 대조 결과 dispatch door는 동등하지 않다(§4.8.2). 권고는 (a) Claude가 intake를 돌리지 않음(D), 필요하면 (b) Claude 전용 프로필·v2 전용 경로(C). door를 그대로 쓰는 (c)는 스케줄 위임 권한까지 열리므로 권하지 않음. W-13: 소스 대조 결과 `ready`는 비동등, `live_entry_possible`은 부분 동등(기록 기반, 한 주기 지연), `execution_stage`는 동등(§4.8.3). 권고는 진입 가능 여부는 read-bridge(B, D-11 이후), 프로세스 `ready`는 D. 기록 기반 답으로 충분한지 Thomas 판단 | 미결정 |
| D-15 | Claude가 후보별 전략 목록(W-15)을 읽어야 하는지 | 읽지 않는다(D). 필요해지면 read-bridge 새 읽기를 정책 변경과 함께 제안 | 미결정 |
| D-16 | 실행기 사후 확인에 "기동 설정 배너"(door uid 허용 목록, worker 검증 플래그, frontdesk 모드)를 고정 패턴으로 넣을지(§4.8.4) | 대안 1(실행기 고정 패턴)을 기동 배너 3종에 권고. 실행기가 생기기 전에는 의미가 없다. 실행기가 거절되면 대안 2(상태 파일 + read-bridge 새 읽기)로 간다. 두 대안 비교는 §4.12.2, 닫힌 패턴 목록 P1–P8·판정 규칙·시험 명세는 §4.15.4 | 미결정 |

## 9. 하지 않은 것

`.env`와 사본의 내용, 런타임 상태, holdings 파일 읽기. Docker 명령과 데몬 접근. preflight, compose 실행. 이미지 빌드·태그. 계정·그룹·파일 권한·소켓·cron·서비스 변경. 실행기 코드 작성. 부정 시험 실행. CLAUDE.md·skill·Guard 변경. 커밋·PR. C1(2026-10-13)에서도 crontab 다시 읽기, cron 이전, `hermes-notify.sh` 이동, read-bridge 허용 목록 변경은 하지 않았다.
