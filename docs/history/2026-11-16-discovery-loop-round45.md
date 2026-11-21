# 2026-11-16 — discovery loop round 45: positioning on ranks 101+, from a fetch of its own

**Shipped (registration and tool; nothing fetched or read in this commit):** `scripts/research/strategy_ladder.py`
(`LOWER_METRICS_*`, `METRICS_FIRST_DAY`, `HOST_COMMANDS`, `lower_metrics_plan`, `lower_metrics_build`,
`tree_digest`, `shared_metrics_digest`, `lower_metrics_seal`, `lower_positioning_market`, `lower_metrics_agree`,
`LOWER_OVERLAP_UNLOADED`, commands `lower-positioning-plan|digest|build|seal`, `lower-gate --only positioning
--null`), `scripts/research/signal_lab.py` (the ranks-101+ positioning strike narrowed to H-M1's two), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §63.

**Why.** Round 44 found one row whose reason was a cost with unread variables behind it: positioning on ranks
101+, struck because its files had been fetched only for top-100 member months.

**Choices that are not obvious.**

- *A root of its own.* M04's screen reads, and fetches into, `archive-metrics/` (its missing list and refresh
  marker as well as the zips), and builds `derived/metrics-daily/` from it. The new files and their daily values
  live beside both, and the seal requires both shared trees to hash the same as before the fetch.
- *Nothing is read until the seal.* Every ranks-101+ command that loads positioning stops without the completion
  marker, so a half-fetched or half-built file can never be read as a sample.
- *Only this tier's daily file is attached.* The shared file is read once, to check that the days both hold carry
  the same values.
- *The window is 210 days and starts at the archive's first metrics day.* A test records the days each positioning
  variable reads, so the window is checked by calling them, not by arithmetic.
- *Round 41's overlap keeps its comparison set.* It loaded no positioning values, and lifting the strike would
  otherwise have changed what that recorded command compares.
- *Host-only commands are named.* Digest, build and seal run outside the sandbox; a test pins that exactly these
  three do.

## Result (2026-11-16 21:12Z)

Read once, all exit 0 (§63.6). The fetch kept 82,645 files; the archive answered 404 for 12,111 (11,909 of them in
2021). The seal found no problems, and M04's inputs hash the same as before. The gates held: H-R1 reproduces, the
first five null books match, and 86,443 shared symbol-days differ in 0 values. Coverage is 0.856–1.000.

**Map: 0 leads. Sweep: 0 leads.** The family is 31 books, with 0 at p < 0.05 against 1.55 expected by chance. The
seven new books have Sharpe −0.928 to −0.337, and the smallest new p is 0.444. The row closes as "read, nothing".

**Where the registration was off.** Seven books stood, not about five, so the one-null allowance for k ≤ 6 did not
apply. Three of those books have months with no book. They count as zero net, while the null books stand in those
months, so those three are not like-for-like with their nulls. They are recorded, not re-read.
