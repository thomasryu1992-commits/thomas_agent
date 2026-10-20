# 제안: Option C C6b — Claude 계정 이전 실행 패킷 (DRAFT v0.1)

**상태:** IMPLEMENTED 2026-10-20 — **C6b 완료:** b1–b8이 모두 끝났다(아래 §6). Claude는 `claude`(uid 2101)로 돈다. b8은 `C6B_CHECKS=PASS`(41행)이고, b7 뒤 재실행도 PASS다. b7은 `.env` 사본 5개를 지웠다. 보관 방식은 §3 b7 절차와 다르며, 이유는 §6에 있다. **이전 상태(b7 외 완료, 2026-10-20):** b2–b6, b1, b8이 끝났고 b7만 따로 확인을 기다렸다. **그 전 상태(실행 중, 2026-10-20):** 실행 승인을 받았다(아래 §6). b2·b3·b4·b5가 끝났고, b6은 사본까지 준비했다. b6의 crontab 설치는 b1 직전 마지막 root 작업이고, 그다음 b1(Mac 접속 사용자 변경)과 b8이 남았다. b7은 따로 확인한다. 준비 작업 P-1–P-6은 모두 끝났다(P-1–P-3·P-4b 적용, P-4 사본, P-5 토큰, P-6 #1258). **이전 상태:** PARTIALLY DECIDED 2026-10-19 — E-1–E-3이 권고안 A로 정해졌다(아래 결정). 준비 작업 P-1–P-6은 각각 승인을 기다리고, P-6(b8 시험 스크립트)은 같은 날 코드 PR 생성 승인을 받았다. C6b 실행은 별도 승인이다. 이 문서는 아무것도 실행하지 않았다. **원래 상태(작성 당시):** DRAFT 2026-10-19 — 결정 3건(E-1–E-3)과 준비 작업 6건(P-1–P-6)이 실행 승인보다 먼저였다.

**결정(2026-10-19):** `결정: C6b E-1=A(b2–b6 → b1 → b8 → b7), E-2=A(claude 계정 새 로그인, 자격 파일 복사 안 함), E-3=A(hermes-notify.sh는 /root에 그대로, claude 사본 제외) (Thomas 2026-10-19)`

**승인:** `승인: C6b 실행 패킷 작성(b1–b8 명령·확인·되돌리기, Mac 쪽 작업, b7 .env 삭제는 마지막 별도 확인; 문서만, 실행 제외) (Thomas 2026-10-19)`

**기준:** origin/main `6cec14d7`, 2026-10-19 호스트.
- **단계의 출처:** C5 계획 §4–§6(`HOST_PRIVILEGE_SEPARATION_C5_PLAN_V0.1.md`).
- **선행 조건 세 가지는 모두 갖춰졌다:**
  - 1 read door: `HOST_PRIVILEGE_SEPARATION_C6B_READ_DOOR_V0.1.md` §5, 운영 검증 O-1–O-7 PASS;
  - 2 배포 주체: Thomas;
  - 3 cron: `HOST_PRIVILEGE_SEPARATION_C6B_CRON_V0.1.md`, K-1–K-4.
- **C6a로 이미 있는 것:** `claude` 계정 2101, 홈 `/home/claude`, 로그인 키 없음.
- **읽은 것:** 호스트 메타데이터와 Claude 설정 파일의 경로 문자열만 읽었다. `.env`·자격 파일·상태 파일의 내용은 읽지 않았다.

**명령에 대하여.** 아래 명령은 단계의 모양을 정하는 초안이다. 실제로 건넬 명령은 실행 승인 뒤 그 시점의 호스트에서 다시 확인(dry-run 또는 임시 경로 모의)하고 건넨다. 2026-10-19 호스트 디렉터리 명령에서 `install -o 10001`이 이름 없는 uid라 실패한 것이 그 이유다.

---

## 0. 한 줄

Claude가 root를 잃는 단계다. 되돌릴 수 없는 것은 b7(`.env` 사본 삭제) 하나이고 맨 마지막에 따로 확인한다. 나머지는 "Mac 앱의 접속 사용자를 root로 되돌리기" 한 번이면 원래대로 돌아간다. 준비 작업 P-1–P-6이 끝나야 C6b 뒤에도 블로그 작업, 측정 기록, Claude 메모리가 이어진다.

## 1. 결정할 것

**E-1. 단계 순서.**
- **A(권고):** b2 → b3 → b4 → b5 → b6 → **b1** → b8 → b7.
  - b2–b6은 root만 할 수 있는 준비라, 지금의 Claude(root) 세션이 Thomas와 함께 준비하고 확인할 수 있다.
  - b1(Mac 앱 접속 사용자 변경)이 실제 전환이다. 전환 뒤 Claude는 root 명령을 하나도 하지 못하므로 그 앞에 모든 준비를 끝낸다.
  - b8 시험이 FAIL이면 b1만 되돌리면 된다.
- B: C5 계획의 번호 순서(b1부터). 전환 뒤 b2–b6을 Thomas가 혼자 해야 한다.

**E-2. Claude 로그인.**
- **A(권고):** `claude` 계정에서 새로 로그인한다(첫 접속 때 앱이 안내). root의 `/root/.claude/.credentials.json`은 복사하지 않는다. 자격 파일이 계정 경계를 넘지 않는다.
- B: 자격 파일을 복사한다. 재로그인은 없지만 root 자격이 그대로 옮겨진다.

**E-3. `hermes-notify.sh`(D-12).**
- **A(권고):** root 사본은 지금 자리(`/root/.claude/scripts/`)에 그대로 둔다.
  - C6b 뒤 `claude`는 `/root`(0700)를 읽지도 고치지도 못한다. 그래서 D-12가 원한 "root만 쓰는 위치"가 자리를 옮기지 않아도 성립한다.
  - root의 at 작업 23개가 `dated-checks`를 거쳐 이 스크립트를 쓴다. 옮기면 그 경로를 모두 고쳐야 한다.
  - b3에서 `claude` 쪽 복사본에는 넣지 않는다.
- B: C5 계획대로 `/usr/local/sbin/`(0700)로 옮기고 at 작업과 cron의 경로를 고친다.

## 2. 준비 작업 (실행 승인 전, 각각 승인)

| # | 일 | 왜 | 성격 |
|---|---|---|---|
| P-1 | K-1 A: `blog-daily-draft.sh`에서 kw-pipeline 실행을 떼어 root cron 한 줄(예: 매일 22:30, 원고 작업 23:02 전)로 만든다. 원고 작업은 그 결과만 읽는다 | 검색광고 키가 `claude`에게 가지 않게 | `~/.claude/scripts`(로컬 git) 수정, root crontab 한 줄 |
| P-2 | K-3 A: window-check·deferral-watch·f9-reading의 출력(`history.log` 등)을 `/var/lib/thomas-measure/`(root:claude 0750, 파일 0640)로 옮긴다. 작업은 root로 그대로 돈다 | C6b 뒤 Claude가 측정 기록을 계속 읽게 | `/root/*/run.sh` 3개 수정, 디렉터리 1개 |
| P-3 | K-4: holdout split 줄 삭제. root crontab의 볼트 백업(`server_scripts_backup.py`)을 root cron 한 줄로 뗀다 | `claude`로 돌면 `claude`의 crontab만 백업된다 | root crontab |
| P-4 | `claude` 쪽 경로 정리. `claude`가 돌릴 스크립트와 Claude hook의 `/root/...`를 `/home/claude/...`로 바꾼 사본을 준비한다(아래 참고) | 그대로면 C6b 뒤 `/root` 접근 거부로 멈춘다 | 사본 수정. root 원본은 그대로 |
| P-5 | GitHub 자격(D-7). Thomas가 `thomas_agent`와 `thomas-obsidian` 저장소 범위의 fine-grained 토큰을 발급한다(Contents·Pull requests 읽기/쓰기, `workflow` 없음) | b4 clone·push, 볼트 push(K-2) | Thomas(GitHub 웹) |
| P-6 | b8 시험 스크립트. §4의 N 시험을 읽기 전용 probe로 묶은 스크립트를 저장소에 넣는다. C4 하네스(`deploy_runner_c4.py`)의 probe 형식을 재사용한다 | 사람이 손으로 열 가지를 치면 빠뜨린다 | 코드 PR |

P-4에서 고칠 대상은 다음과 같다.
- **Claude hook 네 개**(`/root/.claude/settings.json`): `vault-autopush.sh`, `vault-guard.sh`, `session-start-check.sh`, `pgrep-wait-guard.sh`. LOCAL_ONLY guard는 b2의 관리 설정이 맡는다.
- **`claude` cron 두 작업이 쓰는 스크립트:** loc-trend, blog-daily-draft와 그들이 부르는 `vault.sh`, `kw-pipeline.py`(`slotc`만. 검색광고 키를 쓰지 않는다) 등. kw-weekly는 root에 남는다(2026-10-20 정정, cron 분류 문서 12번).
- **범위:** `/root/.claude/scripts`의 65개 중 41개가 `/root/`를 담고 있다. 실제로 고칠 것은 위 대상이 부르는 것만이고, 목록은 P-4에서 만든다.

## 3. 단계 (E-1 A 순서)

### b2 — 관리 설정과 hook (Thomas, root)
```text
install -d -m 0755 /etc/claude-code /etc/claude-code/hooks
install -m 0755 <origin/main 트리>/.claude/hooks/guard-local-only.sh /etc/claude-code/hooks/
install -m 0755 <origin/main 트리>/.claude/hooks/block-main-commits.sh /etc/claude-code/hooks/
install -m 0644 <준비한 managed-settings.json> /etc/claude-code/managed-settings.json
```
- `managed-settings.json`의 내용은 저장소 `.claude/settings.json`의 `hooks.PreToolUse` 두 항목과 같다. 다만 hook 경로를 `$r/.claude/hooks/…` 대신 `/etc/claude-code/hooks/…`로 고정한다.
- 파일이 없거나 실패하면 거부하는 분기(fail-closed)는 저장소 판 그대로 둔다.
- `allowManagedHooksOnly`는 켜지 않는다(C5 계획 b2).
- **확인:** `claude`로 `test -w /etc/claude-code/managed-settings.json`가 거짓이다(N-15). 관리 설정 위치가 문서대로 읽히는지는 b8에서 guard가 실제로 거부하는 것으로 확인한다.
- **되돌리기:** `rm -r /etc/claude-code`. 저장소 hook은 그대로 남는다.

### b3 — Claude 설정·메모리·skill 복사 (Thomas, root)
- 복사할 것: `CLAUDE.md`, `settings.json`(P-4에서 경로를 고친 판), `skills/`, `scripts/`(`hermes-notify.sh` 제외, E-3), `hooks/`.
  - 메모리는 `projects/-root-thomas-agent/memory`(644K)를 `projects/-home-claude-thomas-agent/memory`로 옮긴다. 프로젝트 디렉터리 이름이 작업 경로에서 나오기 때문이다.
- 복사하지 않을 것:
  - `.credentials.json`(E-2);
  - `sessions/`, `session-env/`, `shell-snapshots/`, `file-history/`, `remote/`, `cache/`, `telemetry/`, `backups/`, `settings-backups/`, 다른 프로젝트 디렉터리;
  - `dated-checks/`. at 작업이 root로 쓰므로 root에 남는다.
- 원본은 지우지 않는다. 소유자는 `chown -R claude:claude /home/claude/.claude`.
- **확인:** b1 뒤 새 세션에서 메모리 색인이 읽힌다.
- **되돌리기:** 원본 그대로이므로 없음(`/home/claude/.claude` 삭제로 충분).

### b4 — 저장소·볼트 사본과 GitHub 자격 (`claude`, P-5 토큰)
```text
`su - claude`로 들어가서:
git clone https://github.com/thomasryu1992-commits/thomas_agent.git ~/thomas_agent
git -C ~/thomas_agent config core.hooksPath .githooks
git clone https://github.com/thomasryu1992-commits/thomas-obsidian.git ~/obsidian-thomas
gh auth login --with-token   (Thomas가 토큰을 붙여 넣는다. 값은 기록하지 않는다)
python3 -m venv ~/thomas_agent/.venv && ~/thomas_agent/.venv/bin/python -m pip install -r ~/thomas_agent/requirements-validation.lock pytest
```
- **확인:** `gh auth status`; `git -C ~/thomas_agent log -1`.
- **되돌리기:** 토큰 폐기(GitHub 웹), 디렉터리 삭제.

### b5 — `hermes-notify.sh` (E-3 A)
- 할 일: b3 복사본에 없음을 확인한다.
- **확인:** `claude`로 `/root/.claude/scripts/hermes-notify.sh` 실행 시도가 거부되는지 본다(`/root` 0700).
- **되돌리기:** 없음(바뀐 것이 없다).

### b6 — cron (Thomas, root. P-1–P-3 뒤)
- 바꾸기 전에 `crontab -l > /root/c6-backup-2026-10-19/crontab.pre-b6`로 사본을 남긴다.
- **root crontab에서 뺄 것:** loc-trend, blog-daily-draft(`claude`로 이동). 두 줄이다(2026-10-20 정정: kw-weekly는 root에 남는다).
- **이미 끝난 것(P-1–P-3, 2026-10-20 적용):** holdout split 삭제(K-4), kw-pipeline(`kw-daily.sh`)과 볼트 crontab 백업(`server-scripts-backup.sh`) 추가.
- **`claude` crontab(`crontab -u claude`)에 넣을 것:**
  - loc-trend(`/home/claude/thomas_agent`);
  - blog-daily-draft(`/home/claude/.claude/scripts/`).
- 나머지 줄은 그대로 둔다(분류 문서 §2, 12번 정정 반영).
- **확인:** 각 작업의 다음 실행 기록(로그 파일의 새 줄).
- **되돌리기:** `crontab /root/c6-backup-2026-10-19/crontab.pre-b6`; `crontab -r -u claude`.

### b1 — 전환: 로그인 키와 Mac 앱 접속 사용자 (Thomas)
서버(root):
```text
install -d -m 0700 -o claude -g claude /home/claude/.ssh
grep ' thomas-macbook-2026-08$' /root/.ssh/authorized_keys > /home/claude/.ssh/authorized_keys
chown claude:claude /home/claude/.ssh/authorized_keys && chmod 0600 /home/claude/.ssh/authorized_keys
ssh-keygen -lf /home/claude/.ssh/authorized_keys     # 기대: 한 줄, SHA256:NmnnkWsI…
```
Mac:
```text
ssh claude@<서버> id        # 기대: uid=2101(claude) … groups=2101(claude),2102(thomas-deploy-gate)
```
- 그다음 Claude 앱의 SSH 원격 연결 설정에서 사용자를 `root`에서 `claude`로 바꾼다.
  - 앱의 메뉴 위치는 이 문서에서 확인하지 못했다(미확인).
  - `~/.ssh/config`에 이 서버의 `User root`가 있으면 함께 바꾼다.
- root 로그인 키 자체는 남긴다. 운영은 Thomas가 root로 계속한다.
- **확인:** 새 Claude 세션에서 `id`가 `uid=2101`이다.
- **되돌리기:** 앱 접속 사용자를 `root`로 되돌린다. `claude`의 `authorized_keys`는 남겨도 된다. C6a uninstall은 이 파일이 있으면 `claude`를 지우지 않는다.

### b8 — C6 시험 (`claude` 세션 + Thomas, P-6 스크립트)
`claude` 계정에서 다음이 모두 거부되어야 한다. 원문은 Option C §4.6에 있다.

| 시험 | 거부되어야 하는 것 |
|---|---|
| N-1 | `.env`와 사본 5개 `open()`. 성공하면 읽지 않고 닫고 FAIL로 기록 |
| N-2 | `/var/run/docker.sock` `connect()` |
| N-3 | `sudo -n true` |
| N-4 | `/opt/thomas-deploy`, `/var/lib/thomas-deploy/trees`, `actor/audit.jsonl`에 쓰기 |
| N-5 | 실행기 pid의 `/proc/<pid>/environ` 읽기, ptrace |
| N-6 | 상태 루트 쓰기, holdings 디렉터리 접근 |
| N-13 | 실행기 서비스 `PYTHONPATH`·작업 디렉터리에 `claude`가 쓸 수 있는 경로가 없음(Thomas가 `systemctl show`로 확인) |
| N-14 | `/etc/thomas-deploy/*`, unit 파일 쓰기 |
| N-15 | `/etc/claude-code/managed-settings.json` 쓰기 |

추가로 다음을 확인한다.
- LOCAL_ONLY guard가 관리 설정에서 실제로 holdings 경로 읽기를 거부한다.
- read door: `python3 scripts/claude_read.py schedules`가 `ok`이다.
- 실행기 `inspect_candidate`가 통과한다.
- root로 도는 Claude 프로세스가 0개다(Thomas: `ps -u root -o args | grep -c '[c]cd-cli'`이 0).

**중단:** FAIL이 하나라도 나오면 b1을 되돌리고(접속 사용자 root) 원인을 보고한다.

### b7 — `.env` 사본 5개를 호스트 밖으로 (Thomas, 마지막, 따로 확인)
대상(C5 계획 §1):
- `/root/backups/.env.bak-20260829`
- `/root/backups/.env.bak-20260904-pr2`
- `/root/thomas_agent/.env.bak-20260928-blogmodel`
- `/root/thomas_agent/.env.bak-20260928-vault`
- `/root/thomas_agent/.env.bak-pre-wallet-20261008`

운영 `.env` 자체는 서비스가 쓰므로 그대로 둔다.
```text
서버:  sha256sum <5개> > /root/env-copies.sha256
       tar -C / -cf - <5개 상대 경로> | age -R /root/backups/age-recipients.txt > /root/env-copies-20261019.tar.age
Mac:   scp root@<서버>:/root/env-copies-20261019.tar.age root@<서버>:/root/env-copies.sha256 .
       age -d -i <Mac의 age 키> env-copies-20261019.tar.age | tar -x -C <Mac 보관 위치>
       (보관 위치에서) shasum -a 256 각 파일 → env-copies.sha256과 대조
서버:  대조가 맞다는 Thomas 확인 뒤에만 rm <5개> /root/env-copies-20261019.tar.age
```
- 서버에는 age 공개키만 있다. 복호화는 Mac에서만 된다(Core 백업과 같은 방식).
- **되돌릴 수 없다.** 호스트 밖 사본이 되돌리기다. 그래서 삭제 직전에 Thomas가 대조 결과를 한 번 더 확인한다(C5 계획 §6).

## 6. 실행 기록 (2026-10-20)

**승인:** `승인: C6b 실행(E-1 순서 b2–b6 → b1 → b8; 단계마다 Claude가 그 시점 호스트에서 명령을 검증해 먼저 보여 주고 실행 — root 단계 b2·b3·b5·b6은 Claude가 root로, b4 토큰 입력과 b1 Mac 접속 사용자 변경은 Thomas; 확인 실패 시 그 단계에서 멈추고 되돌리기, b8 FAIL 시 b1 되돌리기; b7 .env 사본 삭제는 제외 — 따로 확인) (Thomas 2026-10-20)`

| 단계 | 결과 |
|---|---|
| b2 | `/etc/claude-code`(root 0755)에 hook 2개(root 0755, main `57d6bfc8` 판)와 `managed-settings.json`(root 0644)을 설치했다. 설정은 저장소 hook 설정과 같고 hook 경로만 `/etc/claude-code/hooks`로 고정했다. 설치 전 단독 시험 7건이 통과했다(guard 허용·거부·파일 없을 때 거부, main 커밋 거부, 그 밖 허용). `claude`는 세 파일 모두 쓰지 못한다 |
| b3 | `/home/claude/.claude`(0700)에 P-4 사본(CLAUDE.md, settings 2개, hooks, skills, scripts)과 글꼴 `/home/claude/.fonts`를 넣었다. `/root`를 가리키는 링크는 0개다. `chown -Rh`로 링크 대상은 건드리지 않았다. 로컬 git에 경로 치환을 커밋했다(scripts `b3dedc3`, skills `9035283`). 메모리는 b1 직전에 복사한다 |
| b4 | Thomas가 `su - claude -c 'bash ~/c6b-b4.sh'`로 실행했다. gh 로그인, `gh auth setup-git`, git 신원, `thomas_agent`·`thomas-obsidian` clone을 했다. 자격 파일은 `claude` 0600이고, 두 원격 모두 인증 조회가 된다. 가상환경은 Claude가 `claude`로 설치했다 |
| b5 | `claude` 사본에 `hermes-notify.sh`가 없다. root 사본은 `claude`가 읽지도 실행하지도 못한다 |
| b6 | `/home/claude/loc-trend/`(run.sh는 REPO·OUT만 바꿈, 기록 72줄 복사)를 준비했다. root crontab에서 두 줄을 빼고 `claude` crontab에 두 줄을 넣는 새 판을 만들었다. b1 직전 마지막 root 작업으로 설치했다. `claude` crontab에는 08:25 loc-trend와 23:02 blog-daily-draft 두 줄이 있다. `claude` 쪽 원고 작업은 앱이 설치하는 실행 파일(`~/.claude/remote/ccd-cli`)을 쓴다. 두 작업의 첫 실행은 아직 확인하지 않았다 |
| b1 | Thomas가 Mac 앱의 접속 사용자를 `claude`로 바꿨다. 새 세션에서 `id`는 `uid=2101(claude) gid=2101(claude) groups=2101(claude),2102(thomas-deploy-gate)`이다 |
| b8 | **`C6B_CHECKS=PASS`(41행, 실패 0)** — `claude` 세션, `python3 -I ~/thomas_agent/scripts/ops/c6b_checks.py --json ~/c6b-b8.json`(main `57d6bfc8` 판). N-1–N-6, N-13–N-15의 거부 행이 모두 `denied`였다. managed guard는 root 소유이고, holdings 읽기를 단독으로 넣었을 때 거부했다. read door `schedules`와 실행기 `inspect_candidate`는 둘 다 `ok`였다. 처음 세 번은 "root로 도는 Claude 프로세스" 한 행만 FAIL이었다. 전환 결함이 아니라 전제조건(옛 root 세션 종료) 미충족이었다. 1차에는 root `ccd-cli`가 11개였다(10-15부터 떠 있던 root 원격 서버의 자식). Thomas가 앱에서 세션을 닫은 2차에도, 앱 목록에 없던 10-16 세션 2개가 남았다. 3차에는 그 둘이 꺼진 뒤 같은 root 원격 서버가 04:10:54Z에 새 세션 하나를 띄웠다. Thomas가 그 세션까지 끄고 4차에 PASS가 나왔다. 그래서 b1을 되돌리지 않았다. 수동 확인도 했다. 이 세션에서 holdings 경로 `Read`는 거부됐다. 다만 guard가 managed·user·project 세 층에 모두 등록돼 있어 실제 거부가 어느 층에서 나왔는지는 메시지로 가릴 수 없다. managed 층의 판정은 스크립트의 단독 공급 행이 근거다. `claude_read.py schedules`는 `ok`였다. **뒤처리:** PASS 시점에 root 원격 서버 2개(`--serve`, `--bridge`)가 아직 떠 있었다. 종료할 때마다 1분 안에 다시 떴다. 원인은 Mac 앱이 같은 서버에 `root@`와 `claude@` 두 연결을 갖고 같은 초에 함께 재접속한 것이었다. Thomas가 앱 쪽을 정리하고 서버를 끈 뒤, 04:25Z 기준 root 원격 서버, root `notty` 접속, root `ccd-cli`가 모두 0이고 b8 재실행도 PASS(41행)다. root 서버가 다시 보이면 먼저 앱의 root 연결을 확인한다 |
| b7 | **사본 5개 삭제(Thomas, root, 2026-10-20)**. §3의 age 보관 절차 대신 Thomas가 다음 근거로 보관 없이 지우기로 했다. 현재 `.env`는 Thomas가 Mac에 백업해 두었다. 사본마다 키 **이름**만 현재 `.env`와 비교했다(값은 출력하지 않음). 사본에만 있는 이름은 `SCHEDULER_TELEGRAM_BOT_TOKEN` 하나였고, `.env.bak-20260829`와 `.env.bak-20260904-pr2`에 있었다. 이 키는 2026-09-04에 `HERMES_BOT_TOKEN`으로 이름이 바뀌었다(#827, `docs/DEPLOYMENT.md`). 두 사본의 값을 현재 `HERMES_BOT_TOKEN`과 비교한 결과는 `같음`이었고(값은 출력하지 않음), 나머지 세 사본에는 이 키가 없었다. 따라서 사본에만 있던 비밀 값은 없다. 다만 이름이 같고 값만 바뀐 키는 이 비교로 드러나지 않는다. 그런 옛 값은 현재 쓰이지 않는 값이라 보관하지 않았다. Thomas 화면에서 `rm -v`가 다섯 줄 모두 `removed`를 출력했고, 남은 것은 `/root/thomas_agent/.env` 하나다. `claude`는 `/root`에 들어갈 수 없어 삭제를 직접 확인하지 못한다. b8 N-1 행은 파일이 없어도 `denied`로 나오므로 삭제의 근거가 아니다. 삭제 뒤 b8 재실행은 PASS(41행)다. Claude는 이 단계에서 `.env`나 사본의 값을 보지 않았고, 비교 명령은 가짜 파일로 먼저 시험했다 |

## 4. C6b 뒤 바뀌는 운영 (C5 계획 §5 요약)

| 일 | C6b 뒤 |
|---|---|
| 배포·rollback | Thomas가 deploy 스킬로(선행 2). Claude는 PR·검증과 실행기 `prepare_candidate` 요청까지 |
| holdings 보드·readiness | Thomas가 읽어 전달(RD-1). Claude는 read door의 `schedules`·`scheduler_events`만 |
| `docker exec`, 상태 파일, root crontab, 호스트 메타데이터 | Claude는 하지 못한다 |
| Telegram 알림 | Claude는 보내지 못한다(D-12). root의 cron·at 알림은 그대로 |
| 측정 기록 | `/var/lib/thomas-measure/`로 읽는다(P-2) |

## 5. 이 문서가 하지 않은 것

- 어떤 단계도 실행하지 않았고, 호스트·Mac·GitHub 설정을 바꾸지 않았다.
- P-1–P-6은 각각 별도 승인이다. C6b 실행도 별도 승인이고, b7은 그 안에서 한 번 더 확인한다.
- Claude 앱의 접속 사용자 변경 메뉴 위치는 확인하지 못했다.
