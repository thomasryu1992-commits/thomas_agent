# The 2026-10-17 deploy, the cutover move and the Option C merges, recorded

**What changed** (documents only).

**Approval.**
- `승인: 배포·cutover 기록 문서 PR 생성(C7 §12·Option C, 병합 제외)`.
- `승인: 배포·cutover 기록 문서 편집에 편집 도구 사용 허용(cutover 명령 인용 포함, 원장·상태·LOCAL_ONLY 파일 읽기 금지 유지, Guard 변경 없음)`.
- Both lines are dated 2026-10-17 in their text. The first was given on 2026-10-17, the second on
  2026-10-19 ("권고안대로 승인").

**Why the editing tool.** The first attempt was a shell heredoc whose text quoted Thomas's cutover command.
The LOCAL_ONLY guard refused it. It was not retried another way, and nothing was written. Thomas then
allowed the edit through the editing tool. The guard is unchanged.

- **`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` (C7).**
  - The status gains a 2026-10-17(3) line.
  - A new §12.7 records:
    - the deploy approval verbatim;
    - the deploy (`candidate-1238`, `rollback-pre-1238`, checks before the promote);
    - what was asserted in the image, including the disclosed `--adopt-ledger` call in a stateless
      container;
    - Thomas's cutover command and its output;
    - the readiness checks at 11:34Z, 12:34Z and about 15:55Z, names and values only.
  - §12.6's list of remaining steps now says they are done.
- **`HOST_PRIVILEGE_SEPARATION_V0.1.md` (Option C).**
  - The status gains a 2026-10-17(2) line.
  - A new §4.25.5 records:
    - the one-off DEPLOYMENT_HOLD lift and what it put into production (#1223, #1224; the runner code
      in the image, unwired);
    - #1240 (CLOCK-SOURCE, BUILD-HOME) and #1241 (install tool) with their C4 runs;
    - #1242's first measurements;
    - what is still missing.
- **`THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md`.** The C7 lines say the deploy and the cutover move are
  done.
- `docs/proposals/STATUS.md` is regenerated.

**Not changed.**
- No decision made or reworded.
- No ledger, state or LOCAL_ONLY file read; the board's aggregate check names and values only.
- No deploy, install or rollback.
