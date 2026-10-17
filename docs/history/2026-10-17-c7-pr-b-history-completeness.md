# C7 PR-B, option C: named skips, Toss continuity, Binance coverage, and the `history_completeness` check

**What changed:**
- `runtime/mvp_runtime/holdings/cash_flows.py`;
- one new test file;
- three existing test files and the shared support module.

**Decisions and approval** (Thomas 2026-10-17):
- `결정: C7 D2 = C안(E-1 unexplained_gap·no_window_before_first로 A안 기각), PR-B는 C안으로 설계`.
  E-1 ran at Thomas's terminal and printed only status words.
- `승인: C7 PR-B(C안: history_completeness, observed_at·cutover 규칙, 이벤트 호환 시험) 구현 및 PR 생성(병합·배포·cutover 이동 제외)`.
- `승인: C7 PR-B의 holdings 코드·시험 파일 편집에 편집 도구 사용 허용(원장·상태·LOCAL_ONLY 파일 읽기 금지 유지, Guard 변경 없음)`.
  This was given after the LOCAL_ONLY Guard refused a shell edit whose text named the holdings package. The
  work stopped and was reported instead of being retried another way.

**Why option C.** Option A needed the Toss windows to tile the stretch since the original cutover. E-1
found an unexplained gap, and no window before the first one, so A's own rejection conditions held (D2
packet §6). Under C the cutover is moved past the moment the checkpoint (PR-A) began. The history since
the new cutover is then provable, and nothing has to be re-read: no backfill, no wider transfer depth,
no E-2 probe, and no 2027-04-01 deadline.

- **Named skips (C7 §4 option (a)).**
  - A fire that records no Toss window now writes `toss_window_skipped` with the stretch and why:
    - `snapshot_unusable`, when there is no cash figure;
    - `cash_point_behind_ledger`, when the cache's cash point is older than the ledger's last window.
  - A Binance source whose cursor fell behind the query depth writes `flow_window_skipped` with the
    stretch it will never read (`beyond_query_depth`). This is the "silently skipped" defect of
    §12.3(3), now named instead of silent.
  - Both are ordinary chained lines with closed required fields and reasons (`_validate`).
  - Each source also keeps, in the state, where its reading began (`covered_from_ms`).
- **Continuity.** `toss_continuity` counts a stretch covered by a named skip as explained; only an
  uncovered gap is `UNEXPLAINED_GAP`. Skips never count as windows, so a cutover covered only by skips
  is still incomplete.
- **`history_completeness`** (§2: a separate readiness key; `ledger_state_consistency` keeps its
  meaning). It is PASS only when all of these hold:
  - the checkpoint was first observed at or before the cutover (`observed_at` ≤ cutover);
  - no detection stands;
  - Toss is `CONTINUOUS`;
  - every Binance history source began reading at or before the cutover, has read past it, and has no
    named skip ending after it.

  Otherwise it is UNCHECKED, and the readiness detail `history.reason` names the first missing piece:
  `no_cutover`, `no_checkpoint`, `ledger_state_inconsistent`, `checkpoint_after_cutover`,
  `toss_<status>`, or `binance_incomplete`. Nothing upgrades it by time or by approval.
- **Readiness.**
  - `ready` now needs this check too. That closes the item §12.4 left open: an UNVERIFIED checkpoint
    beside a ready PASS.
  - `checkpoint_history` reads VERIFIED only when the history check passes. The checkpoint's own basis
    stays `observed`.
- **`set_cutover`** takes an optional `now`, like the module's other writers. Production calls are
  unchanged.

**The path for Thomas, not taken here.**
1. Deploy PR-A and PR-B together, and only when Option A's hold allows.
2. At his terminal, move the cutover past the checkpoint (`--migrate --reason`).
3. Shadow observation (7 days, 7 dates) starts over from the new cutover.

None of this was done; merge, deploy and the cutover move are each a separate approval.

**Tests.**
- New file `tests/test_mvp_runtime_holdings_c7_history.py`, 27 cases. It covers:
  - both kinds of named skip;
  - the continuity table;
  - the Binance depth skip and where reading began;
  - each UNCHECKED reason;
  - option C end to end (UNCHECKED until the cutover moves past the checkpoint, then PASS and VERIFIED);
  - readiness never ready without the history;
  - the readers that predate PR-B, unchanged by the new lines (D1 condition 4).
- C7-8 and C7-11 flip from `test_defect_` to their PR-B expectation: history UNCHECKED and not ready,
  with consistency still PASS.
- Ten earlier readiness tests are about other checks. They hold the new check at PASS through
  `history_whole` and say so in place.
- **Fault injection** (scratch, outside the suite): each of 17 injected faults made a test fail on the
  first pass.

**Not changed.**
- No refusal or adoption: that is PR-C.
- No cutover move, no deploy, no H6b-shadow or H6c state.
- No door, disclosure or snapshot key beyond the readiness `checks` the board already prints.
- No real ledger, state file or exchange was read.
