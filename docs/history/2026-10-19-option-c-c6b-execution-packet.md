# Option C C6b: the execution packet for moving Claude off root

**What changed:**
- A new proposal, `docs/proposals/HOST_PRIVILEGE_SEPARATION_C6B_EXECUTION_V0.1.md` (DRAFT).
- The regenerated `STATUS.md`.

Nothing was run, and nothing on the host, the Mac or GitHub was changed.

**Approval** (Thomas 2026-10-19): `승인: C6b 실행 패킷 작성(b1–b8 명령·확인·되돌리기, Mac 쪽 작업, b7 .env 삭제는 마지막 별도
확인; 문서만, 실행 제외)`.

**What it says.**
- Steps b1–b8 come from the C5 plan. Each has its draft commands, its check and its reversal.
- **E-1** recommends doing every root-only step (b2–b6) first, while the Claude session is still root, then making
  the switch (b1, the Mac app's login user), then the tests (b8). A failed test is then undone by switching the
  app back.
- **E-2** recommends a fresh Claude login for the `claude` account instead of copying root's credentials file.
- **E-3** leaves `hermes-notify.sh` where it is. After C6b, `/root` is closed to `claude`, and 23 of root's at jobs
  use the script through `dated-checks`.
- **Six preparation items (P-1–P-6)** come before the execution approval:
  - the kw-pipeline split;
  - the measurement-log move;
  - the crontab clean-up;
  - the `claude`-side path rewrite (41 of 65 scripts mention `/root/`);
  - the GitHub token;
  - a b8 probe script.
- **b7** puts the five `.env` copies off the host. It encrypts them to the age recipients, checks their hashes on
  the Mac, and only then deletes them. It is the one irreversible step, and it comes last with its own
  confirmation.

The commands are drafts. Each will be re-checked on the host at execution time before it is handed over.

**Decided the same day** (Thomas 2026-10-19): E-1, E-2 and E-3 all as recommended (A). The status line moves to
PARTIALLY DECIDED, because P-1 to P-6 each still wait on an approval.
