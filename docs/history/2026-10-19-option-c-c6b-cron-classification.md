# Option C C6b prerequisite 3: the root crontab's 15 lines, classified by what each script needs

**What changed:** a new proposal, `docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_CRON_V0.1.md` (DRAFT), and the
regenerated `STATUS.md`. Nothing on the host changed.

**Approval** (Thomas 2026-10-19): `승인: C6b 선행 3 cron 15줄 분류(스크립트 코드 읽기, .env·상태 파일 읽기 제외, 결과
문서 PR; 병합 제외)`.

**How it was read.** From `crontab -l`, only the schedules and the script paths were extracted; for the
environment line, only the variable name. The scripts' code was read. `.env` and the state files were not opened.

**Result.**
- 14 jobs and one environment line.
- 10 jobs stay with root: they need docker, `.env`, a state file or the uid-10000 Hermes directory.
- 3 can move to `claude`: loc-trend, kw-weekly and the daily blog draft. Two of them need two decisions first: where
  the Naver search-ad key lives (K-1) and where the vault lives (K-2).
- 1 job is retired: the holdout split finished on 2026-08-08 and has logged "nothing to do" daily since.
- K-3 asks where the three measurement logs Claude reads go once `/root` is closed to it.
- K-4 records the retirement, and moves the root-crontab vault backup to root.

**Decided the same day** (Thomas 2026-10-19, "권고안대로"): K-1 to K-4 as recommended, A, A, A and the clean-up. The proposal's status line moves to DECIDED. The crontab itself changes only at C6b's b6, after C6b is approved.
