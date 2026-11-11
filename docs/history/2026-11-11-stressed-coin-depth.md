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

**The result in one line.** Within one percent of the mid, a stressed day holds about a twenty-first of what an
ordinary day holds; the 3 bps assumption stands at 1k a leg, is about right at the median for 10k with a heavy
tail, and is optimistic about sevenfold at 50k. The spread itself is not in the data.

**Stage 2's floor.** "못해도 연 수익이 15% 이상", on total position ("총액 15%"): for a basket engine, whose sums are
on one side's notional, a twelve-month sum of 30%.
