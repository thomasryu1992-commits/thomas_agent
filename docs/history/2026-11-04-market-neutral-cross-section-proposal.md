# The generator's next step is a pre-registered market-neutral measurement, not another directional template

- **Asked:** Thomas, 2026-11-04, after a strategy review found no forward evidence that the cohort members beat
  their coin-flip twins. He chose to re-design the generator and keep the verification machinery, to measure
  before building, and to keep improving after 2027-03-22 even if nothing confirms. No lane reduction is asked.
- **Why this shape:** the wall is the sample structure, not the template list. Single-coin directional
  strategies are dominated by market direction, so the effective sample is about ten market periods. A
  long-short basket cancels the common factor inside each trade. The cross-sectional work of 2026-07-30 built
  rank features only, and its own record says beta cancels "probabilistically, not enforced". The 2026-08-04
  "cross-section gross ≈ 0" measured that directional form.
- **Checked while writing:** the public archive serves delisted perps (`SRMUSDT`, `FTTUSDT`, and `LUNAUSDT` in
  its listing). `exchangeInfo` omits `SRMUSDT` and `LUNAUSDT`, so a point-in-time universe has to come from the
  archive listing.
- **This PR changes:** `docs/proposals/MARKET_NEUTRAL_CROSS_SECTION_V0.1.md` (DECIDED the same day: Q1 top 10 by
  volume plus symbols Thomas names before the official run, Q2–Q4 as recommended), `STATUS.md`,
  and this entry. No code, factory, schedule or judgement rule changes. The 11-14 mint-share read stays valid.
