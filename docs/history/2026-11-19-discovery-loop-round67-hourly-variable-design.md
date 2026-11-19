# 2026-11-19 — discovery loop round 67: new variables from hourly bars (design only)

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §85, a row in `HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`
and the status line. No code, no return read.

**Why.** Thomas asked for new variables or hypotheses after round 66 closed the weekly top-30 book. Every registered
variable comes from daily files; the hourly bars H-C1 fetched (288 symbols, every coin ever in the month's top 100,
2020–2024) are the one on-disk source never turned into a ranking variable. No new source is called (D1–D3 no).

**Choices that are not obvious.**

- *One definition per concept, at most two variables.* The upper axis family has room for 30 tests (two variables)
  and the top-30 sweep for one book, so windows, lags and hours are fixed before any value exists.
- *N1 price delay and N2 volume concentration.* N1 reads the lag structure against BTC, which its nearest records
  (beta, idiosyncratic volatility: struck; H-C1: closed) do not; it is also noted as an idea formed after H-C1. N2
  reads where in the day trading happens, which daily bars cannot see.
- *Not raised by default.* A session split (weak payer story; the closed replay device held time-of-day variables),
  a post-shock catch-up book (H-C1 is not re-read), hourly illiquidity or volatility (kin of daily or struck ones).
- *Input-only doors first.* Coverage per tier-month (0.80), rank overlap with kin (§40's 0.8), and rank persistence
  decide which of the two reach the map; only then the recorded path (map, sweeps, axis checks).
