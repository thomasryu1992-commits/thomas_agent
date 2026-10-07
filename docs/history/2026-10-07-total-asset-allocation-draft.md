# Total asset allocation drafted: a neutral target mix, a 5/25 band rebalance, and Korean tax placement

- **What this PR adds:** `docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md` (DRAFT) and `STATUS.md`
  regenerated. No code, no schema, no constant, no schedule.
- **Why it exists:** Thomas (2026-10-07) asked for a mathematically reviewed allocation for growing the
  crypto lane into whole-asset management, using equities, crypto spot, futures, options and dividends,
  with Korean tax included. The draft is a child of `MULTI_ASSET_EXPANSION_V0.1.md`. It gives target
  weights and bands to P2, and it supplies D2's "next-best use of the same capital" that options must beat.
- **Shape:** the target is equities 40 · bonds 37 · gold 13 · BTC 5 · cash 5, and the crypto engine gets 0%
  until cohort 1 closes (2027-03-22).
  - BTC is capped by risk contribution (24% at a 5% weight), not by a return forecast. Its 60% volatility
    costs 18%p/yr of compounding, so any μ below that loses.
  - Futures leverage, funding carry and direct options are at 0. Covered calls may replace at most 5 of the
    equity weight, inside tax wrappers only.
  - Rebalancing checks monthly and fires when any asset leaves min(5%p, 25% of its target).
- **Numbers it rests on:** a 10-year weekly simulation, 4,000 t(5) paths, with costs.
  - Neutral returns 4.60% compounded, with a 26.7% 95th-percentile drawdown.
  - Growth (equities 55) adds 0.30%p but adds 8%p to that drawdown. In a 4.5% equity-premium world it adds
    nothing.
  - Account placement saves 0.33%p/yr of tax at 1억 KRW, the same size as the growth premium, at no added risk.
- **Correction carried in the draft:** the chat had called a 0.9–1.2%p "rebalancing bonus" a gain. That
  figure is the diversification return over the weighted average of the assets' own compounding. Against
  buy-and-hold, rebalancing adds 0.02–0.07%p. Its value is risk control: about 6%p off the tail drawdown,
  and BTC held near its target.
- **Tax facts verified on 2026-10-07:** the major-shareholder threshold stays at 50억 (decided 2025-09-15);
  crypto is taxed from 2027-01-01 at 22% above 2.5M KRW, on a 2026-12-31 step-up basis, with no
  carry-forward; KRX gold is exempt; Toss Securities has a pension-savings account but no ISA yet. A blog
  claiming the threshold had been restored to 10억 was wrong. The ISA expansion's status conflicts between
  sources and is modelled on current law.
- **Open:** Q1–Q5. Q3 surfaces a real conflict with D3's "Toss only": without an ISA and KRX gold, the tax
  drag is 0.20%p/yr higher.
