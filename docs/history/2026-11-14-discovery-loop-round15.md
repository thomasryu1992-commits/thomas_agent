# 2026-11-14 — discovery loop round 15: the third remaining variant, the per-coin open-interest flush

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`Flushes`, `flush_book`, `flush_counts`, `flush_run`, `closing_oi`, `crash_days_of`, a `cadence` argument on
`measure` whose default is the weekly one; the commands `flush-counts`, `flush`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §29.

**Choices that are not obvious.**

- *The open interest is the day's last row, not the day mean already derived on disk.* The price leg is close to
  close. A day mean moves a late flush into the next day, when the price condition no longer holds, and enters an
  early one a day late. Reading the 23:55 row keeps both legs on one clock; a loader opens only the files dated
  inside the span (and the day before it), and a test pins that a decision reads nothing after its day's close.
- *The market's days are out by definition, and what is left is still checked against round 10.* H-C1 read the
  rebound after a market-wide crash and is closed with its 72-hour hold never to be changed. A per-coin flush held
  three days would be that book on daily bars wherever the whole market flushes. So a day on which ten or more
  members flush carries no event, and if 40% of the remaining events still fall on H-C1's event days (or the day
  after) the book is not measured. The limit is the one the gap book and the spot-share search used.
- *The stop counts days, not events.* Thirty events on three days are three observations.
- *Three years.* The positioning files barely exist before 2022. On three years rung 2's later-years rule is the
  whole span and rung 3 is looser than it, so rung 3 adds nothing; the registration says so rather than pretending
  to four conditions.
- *`measure` gains a cadence argument rather than a second copy.* The default is the weekly cadence every earlier
  book was read at; a test asserts a book that names none is still decided on Sundays, and round 5 is re-run before
  anything is counted.
- *The structure is a headwind here.* Long alts against short Bitcoin lost about 9% a year net on placebo coins
  over 2020–2024. The size line says so, as round 14's said the mirror was a tailwind.

**Results (same day).**

- *The first count was not read, for a tooling reason.* Open interest was on file for 88.7% of 2024's member
  coin-days, under the 90% stop. The reader took each file's last row as the day's close, as
  `regime_strategies.read_oi` does, and some files are not in time order (758 of April 2024's 1,080). Reading the
  latest stamp of the day recovers 1,039 days. The registration had said a coverage stop means "not read: fix the
  tool, count once", so the definition stayed as it was and the count was run once more: 455 events on 249 days,
  98.5% to 99.9% of the universe on file, 13.8% of events on round 10's event days.
- *H-O1 failed every rung and is closed.* Annual Sharpe -0.676 net over three years, -9.9% of total position a
  year, p 0.581 against the same book on other members that day. The rebound (+8% over three years on the long
  side) was a fifth of the alts' costs (42%).
- *The matched null's p is under 0.05 and counts as nothing.* Against members that fell as much with their open
  interest unchanged the book's p is 0.033: it sits above books built by drawing, for each event, one of the three
  members nearest in that day's move. Those placebo books netted -13.3% a year and the plain ones -7.5%. That gap
  is an observation, not a registered reading, and no test was run on it. No book is built from it: buying the
  drained coins against the other fallers, or selling fallers, would both be hypotheses written after this result.
- *With this the three variants round 12's table left are done: one could not be measured and two failed.* Seven
  measured hypotheses netted between -10.4% and +6.6% of total position a year against a floor of 15%.
- *The record does not say the data on disk is exhausted.* Three things were set aside in writing during these
  rounds: the funding-basis book widened to a per-coin tail, a lower positive-funding threshold, and positioning
  files beyond the top thirty. The second is ruled out by the round's own rule and the third has no hypothesis to
  read it. The first is Thomas's to decide (`FUNDING_BASIS_V0.1` says so) and is an existing read, so it is the one
  question asked. A new source is not asked for: that would re-ask D1 to D3.
- *`regime_strategies.read_oi` has the same last-row reading.* Not fixed here; reported as a follow-up.

