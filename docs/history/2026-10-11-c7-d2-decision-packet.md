# A review-only packet gathers what Thomas needs to choose C7 D2, and decides nothing

- **What this PR adds:** `docs/proposals/HOLDINGS_C7_D2_DECISION_PACKET_V0.1.md` (DRAFT, review only). It
  regroups the record already in `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §12.1–§12.3 and #1212 for one decision. It
  covers:
  - what the Toss continuity diagnostic proves and does not prove;
  - why the Binance older-window probe is needed;
  - the two evidence runs, each needing its own approval;
  - the minimum evidence for option A, and the conditions that rule A out;
  - what option C restarts;
  - the ~2027-04-01 limit, which is not a target date;
  - checkpoint versus history completeness;
  - why D2 does not open H6c.
- **The point it makes explicit:**
  - Production's first Toss window postdates the cutover, so the diagnostic will likely read
    `INCOMPLETE_EVIDENCE / no_window_before_first`. That is derived from the code; the real file was not read.
  - Thomas decided (#1212 final review, decision 6) that this reason is not waived.
  - That outcome would therefore rule out option A's Toss condition unless a new decision is made. The packet
    does not propose one.
- **Correction (2026-10-11, review):**
  - The first packet made "trailing at the normal fire interval" part of option A's minimum evidence. No owner
    decision supports that condition, and #1212's decision 3 declined using trailing to change the verdict.
  - The diagnostic's contract stays as #1212 wrote it, and trailing is reference only.
  - Whether A needs separate evidence of freshness after the last window is now a DECISION_REQUIRED item.
- **What it deliberately does not do:**
  - It neither recommends nor decides A or C.
  - It runs neither evidence step.
  - It touches no real ledger, state file or venue API, and changes no runtime or cutover.
