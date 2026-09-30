# The crypto improvement plan, checked against main: most of it waits on data or a decision, not on code

- **What was found:** an outside improvement plan (forward validation, slippage distribution,
  portfolio correlation, regime evaluation, hypothesis factory, stress cost, gap KPIs, sizing, halts,
  heartbeat, dashboard, plane boundary) was compared section by section with main `70328a10`.
  - Five sections are already built.
  - Five are partly built.
  - Six are held by decisions already taken: review D3, RESEARCH_EPOCH Q3=B, D5, PORTFOLIO_INDEPENDENCE
    Q3 and REMAINING_WORK §L E2.
  - The P0 items all have their machinery. What binds them is data: the host is at PAPER, so the
    slippage sample is n=12 stops and n=3 entries, and forward verdicts need calendar time.
- **One place where the plan's principle and the code differ:** `/pause` and `/kill` skip every due
  fire, so settlement, the time exit and reconciliation stop with them, and only brackets resting at
  the venue protect. It is a documented choice (`control.py`, `docs/DEPLOYMENT.md`). Whether to keep
  it is Q3.
- **What was delivered:** `docs/proposals/CRYPTO_SYSTEM_IMPROVEMENT_GAP_ANALYSIS_V0.1.md` (DRAFT), with
  `STATUS.md` regenerated. It also contains an invariant-to-test table for the plan's §21 and five
  small PRs. Three of them have no precondition and no runtime effect.
- **Deliberately not done:** no code. Q1 and Q2 (whether two read-only report changes fall under
  D3's pause) and Q3 are Thomas's.
