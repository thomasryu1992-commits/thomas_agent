# 제안: Option C C5 — 운영 호스트 변경 계획(C6a 실행기 설치, C6b Claude 계정 이전)과 되돌리기 (DRAFT v0.1)

**상태:** DRAFT 2026-10-19 — C5(운영 호스트 변경 계획 검토)의 계획서다. C5의 완료 기준은 Thomas가 이 계획을 승인하는 것이고, 승인돼도 호스트는 바뀌지 않는다. 실제 변경은 C6a(실행기 설치)와 C6b(Claude 계정 이전)이며 각각 별도 승인이다. 이 문서를 쓰면서 호스트에서는 메타데이터만 읽었다. `.env`와 상태 파일의 내용은 읽지 않았다.

**기준:** origin/main `6047ca27`(#1245), 2026-10-19. 설계 권위는 `HOST_PRIVILEGE_SEPARATION_V0.1.md`(§6 로드맵 C5·C6, §8 결정)와 C2 명세 v0.5다. C4 결과는 `HOST_PRIVILEGE_SEPARATION_C4_PLAN_V0.1.md` §10과 #1240·#1241·#1242의 러너 실행에 있다.

---

## 0. 결정과 승인 (원문)

- `결정: C5 범위 = A(한 계획서에 C6a 실행기 설치·C6b Claude 계정 이전 두 단계, 단계별 되돌리기 포함); D-10b = 없음(promote 구현 시 재결정), RUNNER-FETCH = 첫 설치는 Thomas가 설치 도구 단계로 트리 준비, D-5·D-6·D-7·D-9·D-12·D-13·D-14·D-15·D-16 = 문서 권고 기본값, DESIGN_ONLY (Thomas 2026-10-19)`
- `승인: C5 계획서 작성 및 문서 PR 생성(호스트 메타데이터 읽기 전용 확인 포함 — .env·상태 파일 내용 제외; 호스트 변경·설치·병합 제외) (Thomas 2026-10-19)`

## 1. 지금 호스트 (메타데이터, 2026-10-19 읽음)

| 항목 | 값 |
|---|---|
| 자원 | 2 vCPU, 메모리 3.7GB(사용 가능 약 1.2GB), 디스크 `/` 75GB 중 27GB 여유 |
| 계정 | 로그인 가능한 계정은 `root`뿐이다(uid ≥ 1000 계정 없음). 서비스는 컨테이너 안에서 uid 10001로 돈다. 호스트에는 그 이름이 없어 상태 디렉터리 소유자가 `UNKNOWN`으로 보인다 |
| docker 그룹 | gid 983, 구성원 없음. Docker는 root가 쓴다 |
| sudo | `/etc/sudoers.d/`에 `90-cloud-init-users`, `README`뿐이다 |
| SSH | root가 키 4개로 로그인한다. 비밀번호 인증은 꺼져 있다(`99-no-password.conf`) |
| cron | root crontab 15줄, `/etc/cron.d`는 `e2scrub_all`뿐이다. root crontab이 실행하는 스크립트는 아래 목록이다(경로만 읽었고 인자와 내용은 읽지 않았다) |
| at | 대기 작업 28개(root) |
| systemd | Thomas·실행기 관련 unit은 없다 |
| 도구 | Docker 29.6.2, git 2.53.0, 시스템 Python 3.14.4(실행기에 쓸 3.12.13은 없다), chrony 4.8(커널 동기 확인됨) |
| 실행기 경로 | `/opt/thomas-deploy`, `/etc/thomas-deploy`, `/var/lib/thomas-deploy` 모두 없다 |
| Claude 설정 | `/root/.claude/settings.json`·`settings.local.json`, 저장소 `.claude/settings.json`·`settings.local.json`. 모두 root 소유 0644다. `/etc/claude-code`(관리 설정)는 없다 |
| `.env` | `/root/thomas_agent/.env` 0600 root |
| `.env` 사본 | **5개**(D-6은 3개로 적었다). `/root/backups/.env.bak-20260829`, `/root/backups/.env.bak-20260904-pr2`, `/root/thomas_agent/.env.bak-20260928-blogmodel`, `/root/thomas_agent/.env.bak-20260928-vault`, `/root/thomas_agent/.env.bak-pre-wallet-20261008`. 모두 0600 root. hermes 자체의 `/root/hermes-trial/data/.env`(0600, uid 미등록)는 hermes 서비스 소유라 D-6 대상이 아니다 |
| `hermes-notify.sh` | `/root/.claude/scripts/hermes-notify.sh`(0700 root)와 볼트 사본 `/root/obsidian-thomas/ops/server-scripts/hermes-notify.sh`(0700 root) |

root crontab이 실행하는 스크립트:
- `/root/holdout-split/split_half_test.py`(python3);
- `/root/window-check/run.sh`;
- `/root/deferral-watch/run.sh`;
- `/root/loc-trend/run.sh`;
- `/root/f9-reading/run.sh`;
- `/root/backups/backup-governance-state.sh`(4줄);
- `/root/backups/rotate-hermes-mcp-log.sh`;
- `/root/backups/backup-watch.sh`;
- `/root/backups/health-watch.sh`;
- `/root/.claude/scripts/kw-weekly.sh`;
- `/root/.claude/scripts/blog-daily-draft.sh`.

## 2. 계정과 uid

| 계정 | uid·gid(제안) | 그룹 | 셸·홈 | 쓰임 |
|---|---|---|---|---|
| `claude` | 2101 | `thomas-deploy-gate`(G 소켓에 닿기 위해) | `/bin/bash`, `/home/claude` | C6b부터 Claude 세션. sudo 없음, docker 그룹 없음 |
| `thomas-deploy-gate`(G) | 2102 | `thomas-deploy-actor`(X 소켓에 닿기 위해) | `/usr/sbin/nologin`, `/nonexistent` | G 서비스 |
| `thomas-deploy-actor`(X) | 2103 | `docker` | `/usr/sbin/nologin`, `/nonexistent` | X 서비스. docker 그룹이라 root와 같은 위험이다(CR-12 NOT_PROTECTED, D-10a에서 수용) |

uid는 C4와 같은 값이다. 설정 파일의 `allowed_uids`가 이 숫자를 그대로 쓴다. C6a 직전에 비어 있는지 다시 확인하고, 쓰이고 있으면 멈추고 다른 값을 정한다.

`claude` 계정은 C6a에서 미리 만든다. G의 `allowed_uids`가 이 uid를 가리키기 때문이다. 로그인 키는 C6b에서 넣는다.

## 3. C6a — 실행기 설치

모든 단계는 Thomas가 root로 서버 셸에서 한다. 명령은 C4 러너에서 같은 순서로 실행해 통과한 것(#1241 run 39046415908)을 운영 경로에 맞춘 것이다. C6a 승인 직후, 실행 전에 한 번 더 검토한다.

| # | 단계 | 명령(요지) | 확인 | 되돌리기 |
|---|---|---|---|---|
| a0 | 기록 | `mkdir -m 700 /root/c6-backup-<날짜>`; `/etc/passwd`·`/etc/group`·`crontab -l` 사본 | 파일 3개 | 해당 없음 |
| a1 | 계정 | 그룹을 만든다: `groupadd -g 2101 claude`, `groupadd -g 2102 thomas-deploy-gate`, `groupadd -g 2103 thomas-deploy-actor`. 계정을 만든다: `useradd -u 2102 -g 2102 -M -d /nonexistent -s /usr/sbin/nologin thomas-deploy-gate`, X도 같은 형태. 그룹에 넣는다: `usermod -aG docker thomas-deploy-actor`, `usermod -aG thomas-deploy-actor thomas-deploy-gate`. `useradd -u 2101 -g 2101 -m -s /bin/bash claude`, `usermod -aG thomas-deploy-gate claude` | `id` 세 줄 | `userdel` 세 번, `groupdel` 세 번 |
| a2 | 인터프리터 | python-build-standalone `20260807` 3.12.13 x86_64 `install_only`를 받는다. `sha256sum -c`(`5bd6f36f…cdbbe`) 뒤에만 `/opt/thomas-deploy/<sha>/python`에 푼다 | `python3 --version` = 3.12.13 | `rm -r /opt/thomas-deploy` |
| a3 | 코드 | 깨끗한 origin/main 작업 트리에서 `git archive <sha> runtime schemas scripts`를 `…/app`에 푼다. `python -I -m venv …/venv`, `pip install -r requirements-validation.lock`. `cd …/app && ../venv/bin/python -m scripts.ops.deploy_runner_install link-install --app …/app`. `chown -R root:root`, `chmod -R go-w` | `venv/bin/python -I -c 'import runtime.mvp_runtime.deploy_runner.serve'` | a2와 같다 |
| a4 | 디렉터리 | `/etc/thomas-deploy`·`/var/lib/thomas-deploy`·`…/trees`는 root 0755. `…/gate`는 G 0700, `…/actor`는 X 0700 | `stat` | `rm -r` |
| a5 | 신뢰 파일 | `allowed_signers`: Thomas의 서명 공개키 한 줄, `namespaces="deploy-approval.v1@thomas-agent"`, principal `thomas-deploy-approver`. TYPE-B-KEY K1에 따라 Thomas의 기존 SSH 키다. 어느 키인지는 Thomas가 고르고 공개키만 붙인다. `revoked_keys`·`revoked_approvals`는 비워 둔다. `trust_anchor`는 같은 공개키다. 모두 root 0644 | `ssh-keygen -l -f`로 지문만 대조 | 파일 삭제 |
| a6 | 소비 저장소 | `deploy_runner_install genesis --dir …/actor --install-sha <sha> --owner-uid 2103`. 이 단계는 `docker-config`(0700, X)도 만든다 | 출력 `store_id`를 설정에 쓴다 | `…/actor` 삭제 |
| a7 | 설정 | `actor.json`·`gate.json`, root 0644. 환경은 `{project: thomas_agent, host_label: <정함>, profile: production}`, 지문은 `deploy_runner_install fingerprint`의 출력이다. 실행기 정책(CR 결정): 수명 900, 발급 오차 60, 점프 5, 역행 5, 마감은 모두 300. 소켓은 `/run/thomas-deploy-actor/actor.sock`·`/run/thomas-deploy-gate/gate.sock`. `allowed_uids`는 actor가 `[2102]`, gate가 `[2101]`. G의 actor 마감 60초 | `deploy_runner_install check-config --role …`를 각 계정으로 실행(`setpriv`) | 파일 삭제 |
| a8 | unit | `render-units --out /etc/systemd/system …`. `systemctl daemon-reload`, `enable --now thomas-deploy-actor`, 이어서 gate | 두 unit active, 소켓 0660 | `systemctl disable --now` 두 개, unit 파일 삭제, `daemon-reload` |
| a9 | 첫 확인 | `claude` 계정으로 gate에 `inspect_candidate` 한 번(읽기, 승인 없음). root로 같은 요청을 보내면 `PEER_NOT_PERMITTED`여야 한다 | 응답 code | 해당 없음 |

**C6a가 바꾸지 않는 것:**
- 실행 중인 thomas 컨테이너;
- `.env`;
- 상태 디렉터리;
- root crontab과 at 작업;
- Claude 세션(여전히 root);
- Guard.

**prepare를 실제로 쓰려면:** PR마다 실행기 트리가 필요하다. RUNNER-FETCH 결정에 따라 Thomas가 root로 준비한다.
1. `/var/lib/thomas-deploy/trees/<PR>`에 origin/main을 clone하고 `refs/remotes/origin/main`이 HEAD와 같게 둔다.
2. `deploy_runner_install trust-tree --tree …/<PR>`.

그다음 서명한 승인을 보낸다. 첫 실제 prepare는 C7 계획에 속하므로 이 계획의 범위가 아니다.

**C6a 전체 되돌리기:** a8 → a1의 역순이다. X의 기록 디렉터리는 지우기 전에 `/root/c6-backup-<날짜>`로 옮긴다. 감사 기록을 남기기 위해서다. 운영 서비스에는 아무 영향이 없다.

## 4. C6b — Claude 계정 이전

C6b는 C6a보다 훨씬 넓다. Claude가 root를 잃으면 지금 Claude가 하는 일 가운데 상당수가 경로를 바꾸거나 Thomas에게 넘어간다(§5). 그래서 **아래 선행 조건이 갖춰지기 전에는 C6b를 시작하지 않는다.**

**선행 조건(각각 별도 결정·승인):**
1. **Claude 전용 read door의 운영화(D-11 a1).** Claude는 상태 디렉터리를 직접 읽지 못한다(`/root` 0700, 상태는 uid 10001). 지금 `docker exec`로 하는 읽기(holdings 집계 보드 등)는 Claude 전용 read-bridge 인스턴스로 옮겨야 한다. 코드는 #1223에 있지만, compose 서비스 추가와 배포는 아직이다.
2. **배포 주체의 변경.** `claude`는 docker를 쓰지 못하므로 deploy 스킬을 실행할 수 없다.
   - 실행기가 `promote`를 구현하기 전까지 배포와 rollback은 Thomas가 직접 한다(deploy 스킬 절차 그대로).
   - Claude는 PR과 검증까지 하고, 실행기에는 `prepare_candidate`만 요청한다.
3. **cron 분류.** §1의 15줄을 스크립트 내용으로 분류한다. 분류 기준은 root·docker·`.env`가 필요한지다. 이 계획서에서는 경로만 읽었다. 분류는 C6b 준비의 첫 단계이고 스크립트 내용(코드)을 읽어야 한다.

**단계 (Thomas, root):**

| # | 단계 | 확인 | 되돌리기 |
|---|---|---|---|
| b1 | `claude`의 `~/.ssh/authorized_keys`에 Thomas Mac 키를 넣는다. Mac의 Claude 앱 접속 사용자를 root에서 `claude`로 바꾼다(Mac 쪽 작업) | `ssh claude@host id` | 앱 접속 사용자를 root로 되돌림 |
| b2 | 관리 설정(D-13). `/etc/claude-code/managed-settings.json`을 root 0644로 두고, hook 스크립트는 `/etc/claude-code/hooks/`에 root 0755로 둔다. LOCAL_ONLY guard와 main 직접 커밋 차단 hook을 저장소 사본이 아니라 여기서 실행한다. 문서(code.claude.com/docs/en/managed-settings, settings, hooks)에 따르면 이 경로가 Linux의 관리 설정이고, 관리 설정의 hook은 사용자·프로젝트 설정이 지우지 못한다. `allowManagedHooksOnly`는 켜지 않는다(사용자 hook을 추가로 막을 필요가 없다). 파일을 `claude`가 못 고치게 하는 것은 OS 권한(root 소유)이 맡는다 | `claude`로 그 파일에 쓰기 시도 → 거부(N-15) | 디렉터리 삭제. 저장소 hook은 그대로 남아 있다 |
| b3 | Claude의 설정·메모리·skill·스크립트(`/root/.claude`)를 `/home/claude/.claude`로 복사하고 소유자를 바꾼다. 원본은 지우지 않는다 | 새 세션에서 메모리가 읽힘 | 원본 그대로 |
| b4 | 저장소 사본을 `/home/claude/` 아래에 clone한다. GitHub 자격(D-7)은 지금과 같은 저장소 범위이고 `workflow` 권한은 없다. Thomas가 토큰을 발급해 `claude` 계정에 등록한다 | `gh auth status` | 토큰 폐기 |
| b5 | D-12. `hermes-notify.sh`를 root 소유 위치(예: `/usr/local/sbin/`, 0700)로 옮기고 root의 cron·at만 쓰게 한다. `claude`에서는 Telegram 발송이 사라진다 | `claude`로 실행 시도 → 거부 | 원래 위치로 되돌림 |
| b6 | cron: 분류 결과에 따라 Claude 몫(root·docker·`.env`가 필요 없는 것)만 `claude`의 crontab으로 옮긴다. 나머지는 root에 남는다 | 다음 실행 기록 | root crontab 사본(a0)으로 복원 |
| b7 | D-6. `.env` 사본 5개를 호스트 밖(Thomas Mac, 암호화)에 보관한 뒤 삭제한다. **되돌릴 수 없는 유일한 단계**라 맨 마지막에, 따로 한 번 더 확인한다 | 호스트 밖 사본의 해시 대조 뒤 삭제 | 없음(호스트 밖 사본이 되돌리기다) |
| b8 | C6 시험: `claude` 계정에서 N-1–N-6, N-13–N-15. root로 도는 Claude 프로세스가 0개인지 확인한다 | 시험 기록 | FAIL이면 b1을 되돌리고(접속 사용자 root) 원인 보고 |

## 5. C6b 뒤에 바뀌는 운영

| 지금 Claude가 하는 일 | C6b 뒤 |
|---|---|
| deploy 스킬로 배포·rollback | Thomas가 한다. 실행기 `promote`가 구현되면(C8) 서명 승인으로 실행기가 한다 |
| `docker exec`로 holdings 집계 보드·readiness 읽기 | Claude 전용 read door로 읽는다(선행 조건 1). 없으면 Thomas가 읽어 전달한다 |
| 상태 파일(`schedules.jsonl` 등) 직접 읽기 | 하지 않는다(D-9). read door에 있는 것만 읽는다 |
| `hermes-notify.sh`로 Telegram 알림 | 하지 않는다(D-12). root의 cron·at에 남은 알림은 그대로다 |
| 실행기 감사 기록 | 메타데이터만 읽는다(D-5). 열람 경로는 C6b 준비 때 정한다 |
| 호스트 메타데이터, root crontab | 읽지 못한다. 필요하면 Thomas가 읽는다 |

## 6. 순서와 중단 조건

1. C6a(승인) → 운영과 무관하게 실행기만 떠 있는 상태로 둔다. a9로 확인한다.
2. C6b 선행 조건 1–3이 갖춰짐(각 승인).
3. C6b(승인), b1–b6, b8.
4. b7(D-6)은 C6b 시험 통과 뒤 마지막에, 따로 확인한다.

- **중단:** 확인 열이 하나라도 기대와 다르면 그 단계에서 멈추고 되돌린다. uid가 이미 쓰이고 있으면 a1 전에 멈춘다. C6 시험에서 FAIL이 1건이라도 나오면 Claude 세션을 root 접속으로 되돌리고 원인을 보고한다.
- **되돌릴 수 없는 단계:** b7 하나뿐이다. 다른 단계는 모두 위 표의 되돌리기로 원래 상태가 된다.

## 7. 이 계획이 정하지 않은 것

- `host_label` 값.
- C6b 선행 조건 1(read door 운영화)의 설계와 배포.
- cron 15줄의 분류.
- 실행기 감사 기록을 Claude가 읽을 경로(D-5).
- C7(첫 실제 `prepare_candidate`)과 C8(`promote`, 운영 preflight).
- RUNNER-FETCH의 장기안(실행기가 직접 fetch).

## 8. 하지 않은 것

- 계정·그룹·권한·cron·at·systemd·SSH 변경, 설치, 키 생성.
- `.env`와 사본의 내용, 상태 파일, holdings 파일 읽기. cron 스크립트 내용 읽기(경로만 읽음).
- Docker 명령(이 문서를 위해서는 실행하지 않음).
