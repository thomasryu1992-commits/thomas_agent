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
