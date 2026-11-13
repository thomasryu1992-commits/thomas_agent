# 2026-11-13 — discovery loop round 10: hourly bars, and an event book after crashes

**Shipped (registration and tools; nothing measured in this commit):** `scripts/binance_archive.py` (the monthly
kind `klines-1h`), `scripts/research/strategy_ladder.py` (`Hourly`, `crash_events`, `placebo_times`, the crash book
`L14`, `crash_counts`, `crash_run`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §24.

**Why hourly, and why only this way.** The loop's order for a stall reaches "change the data's period" after
widening the tier. The size arithmetic bounds what hourly bars can be for before any hypothesis is chosen: a book
that turns over daily pays about 58% of total position a year even at 8 bps a unit, so nothing held for hours can
leave the 15% floor. Hourly bars are used only to time the entry of a book that trades rarely and holds for days.

**Why this hypothesis.** A crash has a payer the daily bars cannot see: longs liquidated at market into thin books.
The book buys the fifth of the top hundred that fell most beyond their beta over the day before a 5% four-hour drop
in Bitcoin, sells the fifth that fell least, and holds three days.

**Choices that are not obvious.**

- *The event is counted on Bitcoin's bars alone before anything else is fetched.* Sixty files decide the stop rule;
  four hundred symbols are fetched only if it does not stop. A stop is not answered by a new threshold this round.
- *The judged book carries a Bitcoin leg that cancels its net beta.* The worst residuals in a crash are plausibly
  coins whose beta has just risen, which leaves the book long a market that tends to bounce; placebo hours have no
  such bounce, so the placebo null alone could pass a book that only held the rebound. The unhedged value and
  Bitcoin's own three days after the events are printed beside the verdict.
- *The rank and the entry are an hour apart*, as H-R1's were a day apart, and for the same reason.
- *A coin that stops trading inside the hold leaves at its last open and its loss is the book's.* Dropping it would
  be survivorship in favour of a book that buys the worst-hit coins. A test pins it.
- *Hourly opens are flat arrays.* Bar objects for four hundred coins over five years do not fit the sandbox.
