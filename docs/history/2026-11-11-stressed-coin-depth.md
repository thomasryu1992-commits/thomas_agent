# 2026-11-11 — the depth of funding-stressed coins, measured once

`scripts/research/stressed_coin_depth.py`, `scripts/binance_archive.py` (`kind`, `USER_AGENT`),
`docs/proposals/STRESSED_COIN_COST_V0.1.md`; the size floor of `SCREEN_STAGE2_V0.1` is recorded in the same PR.

**What was asked.** M04's locked-span sum came from coins whose funding settled nine or more times a day, and the
engine charges every coin the same 3 bps of slippage. Thomas answered the draft with "기본값대로": measure it, at
1k / 10k / 50k USD a leg.

**Why member-days and not M04's legs.** The diagnostic's door on the locked span is closed, and a list of M04's
positions is a second read of it. Universe membership and settlement counts were already counted (488 member-days),
so the selection uses those, with a seeded control of the same size.

**Why nothing is applied to a recorded number.** A cost-adjusted M04 would be a return of the spent span under a
changed definition. The tool prints depth and impact only; a test fails if a return-like key appears.

**Why the reader changed.** `bookDepth` is a new daily file kind in the same public bucket, so `fetch` takes a
`kind` (two values, anything else is refused) rather than a second fetch function. The client name no longer
carries the project: Thomas's default answer to the draft's C2.

**The result, and what it cannot say.** On a stressed member-day the thin side holds about 121k USD within one
percent of the mid. From depth alone a 50k leg moves the fill about 21 bps at the median, seven times the engine's
3 bps: there the assumption is optimistic. At 1k and 10k the depth impact is under or near 3 bps, which does not
show the assumption holds, because the spread is not in the data; the reading rule written before the fetch said
otherwise and was wrong on that point. The control class (2.5M USD) shares no coin with the stressed class, so the
twenty-one-fold gap mixes funding stress with coin size.

**Stage 2's floor.** "못해도 연 수익이 15% 이상", on total position ("총액 15%"): for a basket engine, whose sums are
on one side's notional, a twelve-month sum of 30%. The carry measurements already report on capital `2N` (`funding_basis.CAPITAL = 2.0`,
`premium_convergence`'s position "capital 2"), so their figures compare to 15% directly.
