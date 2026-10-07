# Total asset allocation validated on 2005–2026 KRW data: the structure holds, and BTC and Korean equity carry more risk than assumed

- **What this PR adds:**
  - `docs/proposals/TOTAL_ASSET_ALLOCATION_VALIDATION_V0.1.md` (RECORD).
  - §11 of `TOTAL_ASSET_ALLOCATION_V0.1.md`: open questions Q6–Q10 and a design for a target-drift display. That doc moves to
    PARTIALLY DECIDED.
  - `STATUS.md` regenerated.
  - No code, no schema, no constant.
- **Why:** Thomas (2026-10-07) asked to validate and refine the whole-asset strategy now that the Toss feed is live. The Toss
  account holds 145 KRW and no positions, so holdings-versus-target was unmeasurable. This PR validates the strategy itself.
- **Data:** monthly KRW returns, 2005-01 to 2026-09 (261 months; BTC 144). Sources are Yahoo Finance (SPY/EFA/EEM global proxy,
  correlation 0.993 with ACWI; KODEX 200; KTB 3y/10y ETFs; GLD; BTC-USD; USD/KRW) and FRED (KTB 10y yield, Korean and US 3-month
  rates). The gaps are filled conservatively and listed in §2: KOSPI plus 1.8% dividends before 2007, and bonds rebuilt from yields
  before 2009.
- **What held, including 2005–2022 without the 2023–26 boom:**
  - Unhedged global equity: GFC +1.8% versus −10.4% hedged. USD/KRW is a crisis hedge for a KRW investor.
  - Gold at 13%: Sharpe 0.80 versus 0.68 without it.
  - The 5/25 band: BTC peaked at 9.6% of the portfolio versus 27.8% under annual rebalancing; without BTC the band matches annual.
  - The neutral mix returned 9.9%/yr (9.5% after the approximate tax drag) with an 11.1% max drawdown. The worst year was 2022,
    when stocks and bonds fell together.
- **What changed:**
  - BTC's realised volatility is 70%, so a 5% weight is 27% of the risk, above the 25% cap; 4.5% is the most the cap allows.
  - Whatever its future return, BTC at 5% adds 4.3–10.8%p to the 95th-percentile drawdown (12-month block bootstrap, assumed means,
    realised covariance).
  - Korean equity's volatility is 24%, against the single 15% equity the first simulation assumed, so a 10% weight is 21–27% of the
    risk.
  - The 3y KTB alone cut the drawdown by 1.2%p for 0.04%p of return (2011+).
- **Guard against overfitting:** no weight is optimised on this sample. Every recommendation is structural and must also hold
  before 2023.
- **Open:**
  - Q6, BTC weight: recommended 2.5%.
  - Q7, Korean equity weight: hold 10% for now.
  - Q8, KTB maturity: move to 3y.
  - Q9, drift display: display only, inside D4.
  - Q10, where BTC spot and the Binance balance sit on the board: manual entry first; Binance as an "engine margin" class with a
    0% target.
- **Decided in the same PR (Thomas 2026-10-07): Q6–Q10 as recommended.**
  - BTC goes from 5% to 2.5%, and the freed 2.5% goes to cash.
  - Korean equity stays at 10% until the annual review.
  - KTBs move to 3-year.
  - The drift display is display-only, inside D4; it is built in a separate PR.
  - BTC spot is entered by hand, and the Binance balance shows as "engine margin" against a 0% target.
  - The target is now equities 30/10, KTB 37, gold 13, BTC 2.5, cash 7.5. The allocation doc moves to DECIDED.
  - On the historical path the decided mix returned 8.85%/yr against 9.90%, with a 9.3% max drawdown against 11.1%. The gap is this
    sample's 60%/yr BTC. Trades stay at 2.2 a year even though BTC's band narrows to ±0.625%p.
