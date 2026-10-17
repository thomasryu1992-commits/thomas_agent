# C4's first results and C7 PR-C, recorded in their owning documents

**What changed** (documents only).

**Approval:** Thomas 2026-10-17, "C4 실행 결과와 C7 §12의 PR-C 결정 기록을 문서로 남기는 일 … 승인할게". Read as:
this documents PR only. Merge is a separate, head-specific approval.

- **`HOST_PRIVILEGE_SEPARATION_C4_PLAN_V0.1.md`.**
  - A new §10 holds the results of run 39023138344 on #1237's merged head: FAIL 0, PASS 94, BLOCKED 3,
    NOT_TESTED 1, leaks 0.
  - Results are recorded per test ID, as kinds and codes only.
  - It explains why the three are BLOCKED (CLOCK-SOURCE).
  - It records the `-I`/`.pth` finding.
  - It judges §7's completion criterion as not met: BLOCKED items remain and §6's values are not
    measured.
  - Its status gains a dated line.
- **`HOST_PRIVILEGE_SEPARATION_V0.1.md`.** §4.25.4 no longer says the C4 results are missing. It points
  to §10 and says the production host is still NOT_TESTED.
- **`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` (C7).**
  - The status moves to IMPLEMENTED: all five PRs of §10 are on main, and nothing is deployed.
  - A new §12.6 holds the PR-C implementation and merge approvals verbatim.
  - §12.6 records what PR-C built, the adoption's cost (D3 reading), the guard change and the changed
    behaviour.
  - §12.6 lists the operational steps that remain. They are not decisions of this document.
- **`THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md`.** The C7 rows say all of C7 is merged and only the
  operational steps remain.
- `docs/proposals/STATUS.md` is regenerated.

**A correction.** The report for #1237 counted 101 PASS for run 39022491162. Recounting the same lines
gives 94. The record uses 94.

**Not changed.** No decision was made or reworded. No deploy, install, cutover move or adoption.
