# A record of how many regime episodes the first forward verdicts rest on

- **What was measured:** `docs/proposals/FORWARD_VERDICT_REGIME_EPISODES_V0.1.md` (RECORD).
  - Public Binance candles over the factory's own window, with the repository's regime classifier,
    read-only in `thomas-scheduler`.
  - The six forward verdicts (4 CONTRADICTED 4h shorts, 2 UNDERPOWERED 1d longs) come from 2026-08-30
    → 09-23, three to five BTC-1d episodes, and no TREND_DOWN day.
  - Five symbols share the modal label 3.5 of 5 on an average bar.
- **What it found in the code:** `judge_forward` returns CONTRADICTED/UNDERPOWERED before the slice
  test, while CONFIRMED needs about 112 days of slices. Under option A a CONTRADICTED drops a member
  from the leaders with no time-spread requirement. `robustness.regime_breadth` counts labels, not
  episodes.
- **Deliberately not done:** no rule or display change. That would loosen a judgment rule
  (research-epoch boundary, `RESEARCH_EPOCH_V0.1.md` Q3) or add display machinery (paused by review
  D3). The record carries two decision items (E1, E2) for the boundary.
