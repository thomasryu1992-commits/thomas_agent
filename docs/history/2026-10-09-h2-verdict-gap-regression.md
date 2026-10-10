# H2 verdict gap: a regression test across an incomplete stretch

- **What this PR changes:** one test in `tests/test_mvp_runtime_holdings_nav_completeness.py`. No
  runtime code changes.
  - A complete fire sets the peak.
  - Three incomplete fires follow: the wallet gate off, a partial Toss read, no FX rate. Each must be
    `unknown`, send no message whatever the told mark says, leave the peak file alone and start no
    baseline line.
  - The next complete fire, far lower, is compared against the peak that stood before the gap. It is
    `breached`, not `initialized`, and that breach is told.
- **Why:** H2 (#1180) withheld the drawdown verdict from its deploy until the first complete NAV
  (2026-10-08 08:33:54Z), about 4.5 h. That gap was chosen: Thomas kept "wallet after H3·H4". The
  existing tests held each piece: an incomplete fire is `unknown`, does not move the peak and does not
  alert. None ran the whole sequence. A design that dropped the peak during a gap, or read `unknown` as
  `clear`, would have erased or announced a drop the gap hid. Both mutations fail this test, and the
  file's other 16 tests pass either way.
- **Found, not changed:**
  - **A gap is not announced.** `combined.alert` says nothing for `unknown`, and the holdings fire has
    no other message. A stretch with no drawdown verdict shows on the board (`drawdown: unknown`) and in
    `holdings_status`, but nothing tells Thomas it began. Whether it should is a policy question for
    Thomas.
  - **A scope change keeps the old told mark.** `reset_peak` clears `holdings_drawdown_told.json`; the
    `scope_change` path in `combine` does not. Reproduced with synthetic files: a told mark of
    `breached` from the old scope, then the first complete fire under the new scope (`initialized`),
    then a second fire. The second fire sends "낙폭이 한도 안으로 돌아왔습니다" at +0.0 %. That is a
    recovery message for a baseline that never fell.

    **Correction (same day):** this first read called it minor, saying no drop is missed. That is wrong.
    A later check found that the stale `breached` mark also silences a real breach under the new
    baseline: the breach equals the mark, so no edge fires and nothing is sent (about −50 % in the
    synthetic case). The fix is #1206 (`docs/history/2026-10-09-new-baseline-clears-told.md`).
    Production is not known to have hit it: the fire after the scope v1 baseline was a plain `combined
    clear`, and the scheduler ledger shows no drawdown message (as #1206 records).
