# Total asset allocation decided: a neutral target mix, a 5/25 band rebalance, and Korean tax placement

- **What this PR adds:** `docs/proposals/TOTAL_ASSET_ALLOCATION_V0.1.md` (drafted, then DECIDED below) and `STATUS.md`
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
- **Decided in the same PR (Thomas 2026-10-07):**
  - Q1, Q2 and Q4 were adopted as recommended: the neutral mix, the 5/25 monthly band, and the 25% BTC
    risk-contribution cap as a P2 limit candidate.
  - Q3 departs from the recommendation: Toss stays the only brokerage. The 0.20%p/yr tax drag is accepted,
    and the question reopens when Toss ships an ISA, when the ISA expansion passes, or when taxable income
    nears the 20M KRW comprehensive-taxation line.
  - Q5: the engine starts at 0%, is improved, and grows on evidence. The decision does **not** lift the
    research pause (D3 2026-09-26). Improvement before the cohort-1 verdict stays inside `CLAUDE.md`'s
    exemption list.
  - The derived increase path is not ratified: first FORWARD_CONFIRMED, then a 1–2% experiment budget, then
    one step at a time; the engine and BTC spot share the 25% cap; the engine returns to 0 if it fails D2's
    benchmark.
  - The status moves from DRAFT to DECIDED. P2's limits and the first increase question remain.