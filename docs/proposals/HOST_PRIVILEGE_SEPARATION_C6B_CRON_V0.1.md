# 제안: Option C C6b 선행 조건 3 — root crontab 15줄 분류 (DRAFT v0.1)

**상태:** DECIDED 2026-10-19 — **정정(2026-10-20):** 12번 kw-weekly는 `claude`로 옮기지 않고 root에 남는다. `kw-pipeline.py run`을 돌려 검색광고 키가 필요하고, K-1 A가 키를 root에만 두기 때문이다(C6b P-4 준비 중 확인). 이전 가능은 2줄(5·13)이다. K-1–K-4가 권고안대로 정해졌다(아래 결정). 분류 문서는 #1250(`3fce85c3`)으로 병합됐다. 실제 crontab 변경은 C6b의 b6 단계이고 C6b 실행 승인 뒤에 한다. 이 문서가 바꾼 호스트 설정은 없다. **원래 상태(작성 당시):** DRAFT 2026-10-19 — 결정 4건(K-1–K-4)이 Thomas를 기다렸다.

**결정(2026-10-19):** Thomas가 "권고안대로 승인할게"로 아래 줄을 승인했다(줄 문구는 Claude가 제시했다).
- `결정: C6b cron K-1=A(검색광고 키는 root에 두고 kw-pipeline만 root cron), K-2=A(claude 홈에 볼트 사본, push 자격 별도 발급), K-3=A(측정 로그 3개를 /var/lib/thomas-measure root:claude 0750으로), K-4=holdout 줄 삭제·crontab 백업은 root cron으로 (Thomas 2026-10-19)`

**승인:** `승인: C6b 선행 3 cron 15줄 분류(스크립트 코드 읽기, .env·상태 파일 읽기 제외, 결과 문서 PR; 병합 제외) (Thomas 2026-10-19)`

**기준:** origin/main `8d1f19c6`, 2026-10-19 호스트.
- `crontab -l`은 일정과 실행 경로만 뽑아 읽었다. 환경 변수 줄은 이름만 보았다.
- 스크립트는 코드만 읽었다.
- `.env`와 상태 파일(`.runtime_governance_state/`)의 내용은 읽지 않았다.

---

## 0. 한 줄

15줄은 환경 변수 1줄과 작업 14줄이다.
- **root 유지:** 10줄. docker, `.env`, 상태 파일, uid 10000 디렉터리 가운데 하나 이상이 필요하다.
- **`claude`로 이전 가능:** 3줄. 그중 2줄은 K-1(검색광고 키)과 K-2(볼트 위치)가 정해져야 옮길 수 있다. **2026-10-20 정정:** 2줄(5·13). 12번 kw-weekly는 root 유지(§2).
- **폐기:** 1줄. 08-08에 할 일을 마쳤다.

## 1. 분류 기준

C5 계획 §4의 기준을 그대로 쓴다. root·docker·`.env`가 필요하면 root에 남긴다. 여기에 두 가지를 더한다.
- **D-9:** C6b 뒤 Claude는 상태 파일을 직접 읽지 않는다. 상태 파일을 직접 여는 작업도 root에 남긴다.
- **`/root` 0700:** C6b 뒤 `claude`는 `/root` 아래를 읽지 못한다. root에 남는 작업 가운데 Claude가 결과를 읽던 것은 결과를 어디에 둘지 정해야 한다(K-3).

## 2. 분류

| # | 일정(UTC) | 스크립트 | 필요한 것(코드에서 확인) | 분류 |
|---|---|---|---|---|
| 1 | — | `HOME=` 환경 변수 | — | root 유지(`claude` crontab은 자기 HOME을 따로 둔다) |
| 2 | 매일 09:07 | `/root/holdout-split/split_half_test.py` | 상태 파일 직접 읽기, `gh pr comment` | **폐기(K-4).** `.posted`가 2026-08-08부터 있어 매일 "already posted; nothing to do"만 남긴다 |
| 3 | 매일 09:23 | `/root/window-check/run.sh` | `docker exec -i thomas-scheduler python -`, `/root/thomas_agent` git fetch | root 유지. 결과는 Claude가 읽는다(K-3) |
| 4 | 매일 08:22 | `/root/deferral-watch/run.sh` | `docker exec -i thomas-scheduler python -`(스케줄러 원장 읽기) | root 유지. 결과는 Claude가 읽는다(K-3) |
| 5 | 매일 08:25 | `/root/loc-trend/run.sh` | `git fetch`·`git archive origin/main`만 사용. docker·`.env`·상태 파일 없음 | **`claude`로 이전 가능.** `claude`의 저장소 사본(b4)을 가리키게 경로만 바꾼다 |
| 6 | 매일 08:27 | `/root/f9-reading/run.sh` | 호스트 venv로 상태 파일 jsonl 2개를 직접 연다 | root 유지(D-9). 결과는 Claude가 읽는다(K-3) |
| 7, 8, 14, 15 | 07:45 / 일 08:15 / 07:55 / 08:05 | `/root/backups/backup-governance-state.sh` (`core`·`candles`·`transcripts`·`transcripts-trial`) | 상태 디렉터리·`.env`를 age로 암호화해 묶음, `docker exec`(hermes·dispatch-bridge) | root 유지 |
| 9 | 매일 07:40 | `/root/backups/rotate-hermes-mcp-log.sh` | uid 10000 소유 hermes 로그 디렉터리에 쓴다 | root 유지 |
| 10 | 매일 08:00 | `/root/backups/backup-watch.sh` | `.env`의 제어 봇 토큰, 상태 파일 `operator_registration.json`, 백업 디렉터리 | root 유지 |
| 11 | 10분마다 | `/root/backups/health-watch.sh` | `docker inspect`·`docker ps`, `.env`의 제어 봇 토큰 | root 유지 |
| 12 | 토 21:30 | `/root/.claude/scripts/kw-weekly.sh` | Naver 검색광고 키(`.env`에서 이름으로 찾음), 볼트 `git pull/push` | **root 유지(2026-10-20 정정).** 처음에는 "`claude`로 이전 가능, 단 K-1·K-2 뒤"로 분류했다. 그러나 이 작업은 `kw-pipeline.py run`을 돌리므로 검색광고 키가 필요하고, K-1 A는 키를 root에만 둔다 |
| 13 | 매일 23:02 | `/root/.claude/scripts/blog-daily-draft.sh` | 무인 Claude 실행(`HOME=/root`), kw-pipeline(같은 키), 볼트 push, 스크립트·스킬 로컬 git 자동 커밋, root crontab 사본을 볼트에 백업 | **`claude`로 이전 가능, 단 K-1·K-2 뒤.** crontab 백업 부분은 root에 남긴다(K-4) |

합계: root 유지 11줄(백업 4줄 포함, 12번 정정 반영), 이전 가능 2줄(5·13), 폐기 1줄(2), 환경 변수 1줄. (처음 합계는 root 10, 이전 가능 3이었다.)

## 3. 결정할 것

**K-1. Naver 검색광고 키(12·13번).** `kw-pipeline.py`는 키를 `~/.config/blog-kw/searchad.env`에서 먼저, 없으면 배포 `.env`에서 찾는다. 지금 전용 파일은 없다. C6b 뒤 `claude`는 `.env`를 읽지 못한다.
- **A(권고):** kw-pipeline 실행만 root cron으로 떼어 낸다. 원고 작업보다 먼저 돌려 결과를 볼트에 쓰고, `claude` 쪽 원고 작업은 그 결과만 읽는다. 키가 `claude` 계정에 가지 않는다. Option C의 목표(Claude 계정에 비밀 없음)와 맞는다.
- B: `claude` 홈에 전용 키 파일(0600)을 둔다. 스크립트는 그대로지만 비밀 하나가 Claude 계정으로 간다.

**K-2. 볼트 위치(12·13번).** 볼트는 `/root/obsidian-thomas`에 있다. 쓰는 곳은 kw-weekly, 원고 작업, 볼트 자동 push다.
- **A(권고):** `claude` 홈에 볼트 사본을 하나 더 clone한다. 볼트 push 자격(배포 키)은 `claude` 몫으로 따로 발급한다. root 쪽 사본은 root 작업(K-1 A의 kw-pipeline, K-4의 crontab 백업)이 쓴다. 같은 원격에 두 사본이 push한다. 지금도 자동 push와 원고 작업이 잠금(`vault-commit.lock`)을 나눠 쓰는데, 사본이 둘이 되면 이 잠금은 공유되지 않는다. 그래서 각 push는 기존대로 `pull --rebase` 뒤에 한다.
- B: 볼트를 `/home/claude`로 옮기고 root 작업이 그쪽을 쓴다. root가 `claude` 홈에 쓰게 되어 소유권이 섞인다.

**K-3. Claude가 읽던 측정 결과(3·4·6번).** window-check, deferral-watch, f9-reading의 결과(`history.log`)는 Claude 세션이 읽어 왔다. C6b 뒤에는 `/root` 아래라 읽지 못한다.
- **A(권고):** 세 작업의 출력만 `/var/lib/thomas-measure/`(root:claude 0750, 파일 0640)로 옮긴다. 작업은 root로 돈다. 출력은 측정값이며 비밀이 아니다. 상태 파일 자체는 여전히 Claude에게 닫혀 있다(D-9).
- B: Claude 전용 read door(선행 조건 1)에 이 세 가지 읽기를 추가한다. 더 좁지만 read door 설계가 끝나야 한다.

**K-4. 정리 두 가지.**
- 2번(holdout split)은 할 일을 마쳤으므로 crontab에서 지운다. 스크립트와 보고서는 남긴다.
- 13번 안의 root crontab 볼트 백업은 root cron으로 옮긴다. `claude`로 돌면 `claude`의 crontab만 백업된다.

## 4. 이 문서가 하지 않은 것

- crontab도 스크립트도 바꾸지 않았다. 이전은 C6b의 b6 단계이고 C6b 승인 뒤에 한다.
- at 대기 작업 28개는 분류하지 않았다(C5 계획 §1에서 이 범위 밖). C6b 준비에서 따로 본다.
- 볼트 자동 push(`vault-autopush`)가 어디서 도는지는 이 분류에 넣지 않았다. root crontab 15줄에 없기 때문이다. K-2를 정할 때 함께 확인한다.
