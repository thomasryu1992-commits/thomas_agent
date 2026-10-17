# 제안: Option C C4 — 비운영 환경에서 분리 검증 계획 (DRAFT v0.1)

**상태:** DRAFT 2026-10-16 — C4(운영과 무관한 환경에서 계정·권한 분리를 시험)의 계획이다. 환경(D-8), 그 환경의 자원·비용, 그리고 C4 전에 지어야 할 서비스 진입점(C3b)의 구현 승인이 정해지지 않았다. 이 문서는 아무것도 실행하지 않고 어떤 권한도 주지 않는다.

**기준:** origin/main `4680754f`(C3a-2 #1228), 2026-10-16. 설계 권위는 `HOST_PRIVILEGE_SEPARATION_V0.1.md`(§4.6, §6 C4행)와 C2 명세 v0.5(명세 8, 8.1–8.3)다. 명세는 #1230이 저장소에 옮기는 중이다. 호스트, 운영 파일, `.env`, Docker에는 접근하지 않았다.

---

## 0. 한 줄

C3의 합성 시험이 증명하지 못하는 것만 C4에서 시험한다. 계정, 파일 권한, 소켓 peer, 실제 Docker 소켓 접근, 설치본 무결성이 그것이다. 운영 호스트도, 실제 `.env`도, 실제 키도 쓰지 않는다.

## 1. C4가 증명하는 것과 하지 않는 것

| 구분 | 내용 |
|---|---|
| 증명하는 것 | 같은 커널 위에서 `claude`·G·X 세 계정이 서로의 파일·소켓·프로세스에 닿지 못한다는 것. 설치본·신뢰 파일·소비 저장소를 비root가 바꾸지 못한다는 것. 실행기가 설치본 밖에서 import하지 않는다는 것 |
| 증명하지 않는 것 | 운영 호스트의 상태(C6에서만). X 침해에 대한 방어(CR-12: NOT_PROTECTED). 실제 이미지 빌드의 Secret 노출(추적 파일 내용은 NOT_VERIFIED) |
| 원칙 | C3 합성 PASS는 C4 증거를 대신하지 않고, C4 PASS는 C6 증거를 대신하지 않는다(명세 8) |

## 2. 선행 조건

| # | 조건 | 상태 |
|---|---|---|
| P-1 | C3a-1·C3a-2 병합 | 완료(#1227 `8358369d`, #1228 `4680754f`) |
| P-2 | C3a-3(G·X·소켓·argv) 병합 | #1229. 2026-10-17 계약 정합 수정 뒤 병합 승인 대기 |
| P-3 | **C3b: 서비스 진입점.** 설치 설정(root 소유 파일)을 읽어 `ActorConfig`·`GateConfig`를 만들고, 시작 대조(`startup.reconcile`)를 돌리고, 두 소켓을 연다. 설치 도구가 소비 저장소 genesis를 쓴다. systemd unit 두 개(`python -I`, 고정 cwd, 최소 env) | **없음.** C3a에는 진입점이 없다. 구현 승인 필요(§8) |
| P-4 | 환경(D-8)과 자원 | 미결정(§3) |
| P-5 | 시계 동기 출처(CLOCK-SOURCE) | 미결정. 없으면 `prepare_candidate`는 `CLOCK_UNTRUSTED`다. C4는 그 거부 자체를 시험하고, 출처 후보를 측정한다(§6) |

## 3. 환경 (D-8)

| 대안 | 장점 | 단점 | 판단 |
|---|---|---|---|
| **V1 임시 VM**(별도 커널, 안에 Docker 데몬) | 계정·`/proc`·ptrace·Docker 소켓 권한이 운영과 같은 모양이다. N-2·N-5·N-13·N-14를 그대로 시험한다 | 자원. 이 호스트는 메모리 3.7GB 중 사용 가능 약 1.3GB(2026-10-16 측정)라 VM을 함께 띄우기에 빠듯하다. 외부 VM은 비용이 든다(금액 미확인) | **권고** |
| V2 rootless 컨테이너 안 systemd | 자원이 적다 | 사용자 namespace가 uid·`/proc`·ptrace 의미를 바꾼다. 컨테이너 안 Docker는 중첩이라 N-2·N-5 결과가 운영을 대표하지 못한다 | 비권고 |
| V3 운영 호스트에 임시 계정 | 자원 없음 | 운영 호스트 변경이다(C5·C6 범위). Option A와 DEPLOYMENT_HOLD에 걸린다 | 제외 |

- **권고:** V1. 위치는 Thomas가 정한다: 이 호스트(메모리 부족 위험), Mac의 로컬 VM, 또는 임시 클라우드 VM(비용).
- VM에는 운영 저장소의 Secret, 운영 이미지, 실제 키, 운영 상태 파일을 넣지 않는다. 코드는 검토된 origin/main SHA만 복사한다.

## 4. 시험 환경 구성 (VM 안)

| 항목 | 구성 |
|---|---|
| 계정 | `claude-c4`(Claude 세션 대역), `thomas-deploy-gate`(G), `thomas-deploy-actor`(X). D-10a에 따라 모두 비root. docker 그룹은 X만 |
| 설치본 | `/opt/thomas-deploy/<sha>/`, root 소유 0755. unit은 `python -I`로 이 경로에서만 import한다 |
| 신뢰 파일 | VM 안에서 만든 **합성** ed25519 키의 `allowed_signers`(principal `thomas-deploy-approver`, `namespaces=` 제한, TYPE-B-KEY K1), 빈 `revoked_keys`, 설치본 앵커(T3). 모두 root 소유. 합성 키는 VM 밖으로 나가지 않는다 |
| 소켓 | `/run/thomas-deploy/gate.sock`(접근 `claude-c4`만), `/run/thomas-deploy/actor.sock`(접근 G만). uid 목록은 unit의 리터럴. `MVP_BRIDGE_CLIENT_GID`는 설정하지 않는다(C3a-3 history의 상속 주의) |
| X 기록 | `/var/lib/thomas-deploy/actor/` 0700 X 소유. genesis는 설치 도구가 쓴다 |
| G 기록 | `/var/lib/thomas-deploy/gate/` 0700 G 소유. X는 읽지 못해야 한다(GAPX-11) |
| 가짜 `.env` | 감시값(예: `C4_SENTINEL_…`)만 든 파일. 운영 경로 모양으로 두고 mode 0600 root. 어떤 응답·감사·로그에도 감시값이 나오면 FAIL |
| Docker | VM 안 데몬. 운영 이미지 대신 작은 합성 이미지에 `thomas-agent-runtime:latest` 태그. 여덟 서비스 이름의 컨테이너를 compose project 라벨 `thomas_agent`로 띄운다 |
| 자기 트리 | `/var/lib/thomas-deploy/trees/<PR>`에 합성 저장소(git). origin fetch는 하지 않는다(RUNNER-FETCH 미결정) |

## 5. 시험 목록

C3에서 이미 합성으로 다룬 항목은 C4에서 **권한과 실제 소켓 아래에서** 다시 확인할 것만 남긴다.

| ID | C4에서 하는 것 | PASS | 증거(값 아님, 종류만) |
|---|---|---|---|
| N-1 | `claude-c4`로 가짜 `.env` `os.access`·`open` | `PermissionError` | 예외 종류 |
| N-2 | `claude-c4`·G로 Docker 소켓 `connect()`만 | 둘 다 `PermissionError` | 같음 |
| N-3 | `claude-c4`·G·X로 `sudo -n true` | 비0 종료, 프롬프트 없음 | 종료 코드 |
| N-4 | `claude-c4`·G·X로 설치본·트리·X 감사에 쓰기 | 모두 거부. X의 자기 기록 디렉터리 쓰기만 허용 | 같음 |
| N-5 | `claude-c4`·G로 X pid의 `/proc/<pid>/environ`, ptrace attach | 거부 | 같음 |
| N-6 | `claude-c4`로 합성 상태 루트·holdings 모양 디렉터리 쓰기·`os.access` | 거부 | 같음 |
| N-7–N-12 | `claude-c4`가 **gate.sock으로** 같은 입력을 보냄(C3 합성의 재확인) | C3와 같은 code, Docker 효과 0. N-11은 프로세스 둘이 동시에 | 응답 code, 감사 행 |
| N-13 | `claude-c4` 쓰기 가능 경로에 같은 이름 모듈. unit 환경·`sys.path` 확인 | 설치본 밖 import 없음 | `sys.path` 사본 |
| N-14 | `claude-c4`·G로 `allowed_signers`·revoked·`consumed.jsonl`·unit 쓰기 | `PermissionError` | 예외 종류 |
| N-15 | — | D-13(관리 설정 위치) 뒤 C6. C4 범위 밖 | — |
| C2X-02·13 | 신뢰 파일을 바꾼 뒤 시작·검증 | `TRUST_ANCHOR_MISMATCH` | code |
| C2X-04 | 같은 승인 동시 요청(프로세스 여럿, 실제 소켓) | 소비 1회 | 소비 저장소 행 수 |
| C2X-06·N1X-12 | CONSUMED 직후 X를 `SIGKILL` → 재시작 | `RECONCILED_UNKNOWN`, LATCH, 모든 요청 `RUNNER_LATCHED` | 감사 행 |
| C2X-11 | umask 000에서 X 기록 쓰기 | 파일 모드 0600 유지 | `stat` |
| C2X-12 | 다른 Python 버전으로 unit 시작 | `RUNNER_ENVIRONMENT_MISMATCH`(C3b가 검사를 넣을 때) | code |
| N1X-03 | `claude-c4`로 actor.sock 직접 연결 | 소켓 파일 권한에서 거부. 연결되더라도 `ACTOR_PEER_NOT_PERMITTED`(명세 6.1b, X의 응답 형식) | 예외 종류, code |
| N1X-16 | G 쪽 신뢰 사본 변조 | X 판정 무변화 | code |
| N1X-17 | X 계정으로 설치본 쓰기 | 거부(D-10a: X 비root) | 예외 종류 |
| N1X-18 | X 침해 시 Docker 권한 상승 | **시험하지 않는다.** NOT_PROTECTED의 문서화 대상이다. 시연은 별도 승인 | — |
| GAPX-11 | X로 G 기록 디렉터리 읽기 | 거부 | 예외 종류 |
| 누출 | 모든 시험 뒤 감사·요청 로그·journal에서 가짜 `.env` 감시값과 합성 키 문자열 검색 | 0건 | 건수 |

## 6. C4에서 잴 값 (CR-8b, CR-10·11·19)

| 값 | 재는 법 | 쓰는 곳 |
|---|---|---|
| Python 정확한 버전과 설치 방법 | VM에서 설치 절차를 끝까지 수행 | CR-8b |
| `prepare_candidate` 마감 시간 | 합성 이미지 빌드 시간 분포 | CR-10·19(승인 수명 ≥ 최장 마감) |
| 시계 동기 출처 | `timedatectl show -p NTPSynchronized` 등의 읽기 결과와 실패 모양 | CR-11, CLOCK-SOURCE |
| 소켓 왕복 지연 | G→X 왕복 | G의 actor 마감(`actor_deadline_s`) |

## 7. 완료 기준과 중단

- **완료:** §5의 C4 항목이 모두 PASS이고, 누출 0건이며, §6의 값이 기록됐다.
- **중단:** FAIL 1건. 원인을 보고하고, 고치는 일은 C3 코드 PR로 따로 간다.
- 결과는 시험 기록 문서로 남긴다. 그 문서에는 값이 아니라 결과 종류만 적는다.

## 8. 정해야 할 것

| ID | 결정 | 권고 | 상태 |
|---|---|---|---|
| D-8 | C4 환경 | V1 임시 VM. 위치는 Mac 로컬 VM 또는 임시 클라우드 VM(이 호스트는 메모리 여유가 작다) | 미결정 |
| C4-RES | 자원·비용 | V1 위치에 따름. 클라우드면 금액을 먼저 확인 | 미결정 |
| C3b | 서비스 진입점·설치 도구·unit 구현과 PR | §2 P-3 범위로 구현 승인. 운영 설치는 제외 | 미결정 |
| CLOCK-SOURCE | 시계 동기 출처 | §6에서 재고 정한다 | 미결정 |
| RUNNER-FETCH | 자기 트리 fetch | C4에서는 쓰지 않는다. env 게이트 능력으로 따로 설계 | 미결정 |

## 9. 하지 않은 것

VM·계정·소켓·unit 생성, Docker 실행, 시험 실행, 키 생성. 운영 호스트, `.env`, 상태 파일, holdings 파일 접근. 이 문서는 계획이고 실행 승인이 아니다.
