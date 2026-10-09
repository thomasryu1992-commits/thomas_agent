# A new baseline starts with nothing told

- **What this PR changes:**
  - `holdings/combined.py`: when `combine` starts a baseline itself (`scope_change` or
    `first_complete`), it removes `holdings_drawdown_told.json`. The removal comes after the baseline
    line and before the peak is written. `reset_peak` already removed the mark; `after_reset` is
    unchanged.
  - Tests in `tests/test_mvp_runtime_holdings_baseline_log.py`: S1 scope change, S2 first complete,
    S3 a real breach under the new baseline, S4 explicit reset, and a stop between the mark and the
    peak.
- **Why:** this was found during the H2 gap check (#1204). The told mark belongs to the baseline it was
  told under. A mark of `breached` left from the old baseline judged the new one:
  - an unchanged second fire sent "낙폭이 한도 안으로 돌아왔습니다" at +0.0 %;
  - **a real breach under the new baseline sent nothing**, because its state equalled the stale mark.

  Before the fix, S1, S2 and S3 failed (S3: no edge at about −50 %).
  Production never hit it: the scope v1 baseline (2026-10-08 08:33:54Z) was followed by a plain
  `combined clear` at 09:33:49Z, with no told suffix. The scheduler ledger shows no drawdown message
  at all.
- **Shaped this way because:**
  - Removing the mark is what `reset_peak` does. `read_told` already reads an absent mark as `clear`,
    so no format changes.
  - The mark goes before the peak. A process stopped in between leaves no peak, so the next fire starts
    the baseline again (one more `baseline_set` line, as any stop there already caused). Reversing the
    order would leave a peak judged against the stale mark. A mutation with the order reversed fails
    the stop test; one without the removal fails four tests.
  - Duplicates are unchanged. The initializing fire is `initialized` and never alerts, so nothing is
    marked in that fire. The mark still moves only after delivery (`store.mark_told`).
- **Not changed:**
  - The fire and `holdings_board --reset-peak` still share no lock over the peak and the mark. That
    is a race that predates this change, and the change does not widen it.
  - The mark is not recorded in the baseline line.
