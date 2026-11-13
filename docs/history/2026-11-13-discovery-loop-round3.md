# 2026-11-13 — the discovery loop's third round: a funding-aware book passes the development rungs, and no scenario axis beats its placebos

`scripts/research/strategy_ladder.py` (`risk_weights(funding=…)`, `is_decision`, `simulate_held(every=…)`),
`scripts/research/signal_lab.py` (`axis_check`, `separation`, `shuffled_spells`, `inventory`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §14.

**What it is for.** After two rounds every candidate had died at the entry rule or the confirm span, so by the
loop's own order the rework went to the strategy logics. The second round's best book paid about 5.7% a year in
funding, and the map's 28-day cells held for its variable.

**Two logics.** L9 leaves out a position that would pay more than 30 bps a week in funding at its last week's rate
(half the lead's own weekly size on the map: a position paying that away has nothing left); ranks are taken before
the filter so nobody crosses the middle. L10 decides every fourth week from a fixed anchor.

**Which candidate may go on was named before any was measured.** This was the sixth build of one lead; the best of
six looks better than one of one. So the registration named the single candidate that could go to the confirm span
(the most reworked, C007, else C005, else C006), and the others close where they fall.

**What came out.** The funding filter turned five years of funding from −28.4% to +3.4% and removed the losing
years. Holding four weeks without it was worse (−45% in 2021). Both together cleared the entry rule and the stability
rung: 1.051, no losing year. Three things bound that number. Random variables built into the same logic average
0.31 — leaving out funding payers and hedging the market earns something by itself — and one of twenty beat it. The
filter was thought of after the first confirm read showed how much funding 2025–2026 charged, so the confirm span is
not independent evidence for this book. And the margin over 1.0 is small.

**The first axis check, and a flaw found on first use.** Each scenario axis is compared with 200 placebo axes made
by shuffling its spells. In the first run every axis sat at or under its placebos' mean and one under all 200 — which
a real axis cannot be by chance. The placebos had moved the unlabelled first months (before an axis has a history to
compare with) into labelled territory and were credited with the difference between those months and the rest. A
placebo now shuffles spells among the labelled decisions only; the first run's figures are kept beside the corrected
ones. Corrected, no axis passes its placebos' 95th percentile: averaged over the 24 variables, splitting by scenario
does not separate them better than spells placed anywhere. The average can hide an axis that matters for two
variables; the per-variable comparison is the next check.

**The first data inventory.** The daily kline files hold four columns no loader keeps (volume, trade count, taker
buy volume and taker buy quote volume) for 953 symbols — a taker-buy share and an average trade size for every
tier, where the positioning files cover only the top coins.

**Not done.** The confirm span was not read. Whether to spend the second of five reads on C007 before the month the
budget set has passed is asked.

## The second confirm read, the same day

Thomas chose to spend the second of five reads on C007 at once rather than a month later (the pacing was his own
default; the bar for a second read, 1.245, was kept). The definition was pushed, the five checks passed on that
commit, a counts-only run showed 22 four-week decisions with thirty coins ranked at each and neither side ever
empty, and it was read once.

**It failed: 0.08 against 1.245.** Nine of twenty random-variable books built with the same logic did as well, so on
2025–2026 the volatility ranking added nothing to this book. Before costs and funding the book made about 31% in 21
months; funding took 19.7% of it — filtered at the decision and held four weeks, a position whose funding changed in
between stayed. On the development years the same book's funding had been −1.7% in five years.

**Two reads, two fails, one cause in common.** Both candidates passed 2020–2024 and both lost 2025–2026 to funding,
which went from 1.7–5.9% over five years to 19.7–39.6% over 21 months. Together with the coins that move several
hundred percent in a day, this says the two spans' universes are different, and that is a reason development
passes do not carry over. Measuring how different — the character of the universe by month, no candidate's return —
would make the three reads left less independent, so it is asked rather than done.

**Also said that day.** Thomas: until a meaningful result comes out, the weekly and monthly steps keep running, by
hand if needed. The cadence in the proposal is a floor, not a wait.

