# 2026-11-09 — Funding basis screen: a day the archive left out is not a day the perp stopped trading

**What.** The USD-M monthly kline archive has no 2026-10-28 row for any of the 639 perps alive across it.
Their index, premium and mark bars and their funding settlements exist for that day. v0.2.1 counts a day as
traded only when the archive has its kline, so it would read such a day as a delisting. Every member would be
closed the day before and reopened the day after, paying two round trips for a day the perp traded.
`scripts/research/funding_basis_v021_screen.py` now treats a day missing for at least 25% of the perps alive
across it as an archive hole, and counts it as traded for every perp with klines on both sides. The proposal is
`docs/proposals/FUNDING_BASIS_V0.2.md` §10. Thomas took both defaults the same day (Q1 the rule, Q2 the 25% share).

**Why the screen and not the v0.2.1 simulator.** The v0.2.1 entry run is judged. The same holes existed inside
its span: 2022-02-26..28 and 2022-04-01..02, with about 34% of perps missing and 13–14 of 30 members each
month. That run read them as untraded. Its code stays, so the judged result stays reproducible. The rule
corrects only the set of traded days the forward screen reads.

**Why a cross-sectional share.** A halt or a delisting happens to one symbol. A quarter of the market losing its
kline on one day, while every derivative series prints, is a publication defect. The derivative bars are the
second witness: a hole with no bars still falls to v0.2's missing-data exclusion, so a low share cannot make the
screen hold a perp with no data. The 25% was chosen after seeing 100%, 34% and under 1%. No day falls between 1%
and 34%, so any share in that range reads the history the same way.

**Why interior only.** A day after a perp's last kline is the LUNA case, where the bars outlive trading. A hole
needs a kline on both sides, so that rule is untouched. A hole on the window's last day waits as NOT_YET until
the next month's klines show the perp traded on.

**Why the refresh warns.** `funding_basis_refresh` prints the month's holes, so the next one is seen when the
month is fetched in early December, not at the official screen in April. It does not halt: the screen already
handles the day.
