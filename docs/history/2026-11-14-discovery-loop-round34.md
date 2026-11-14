# 2026-11-14 — discovery loop round 34: H-R2, the month's reversal on ranks 31–100

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py` (`MidTier`,
`mid_counts`, `mid_run`; commands `mid-counts`, `mid`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §48.5 and
§49.

**Why.** The map prints how many cells meet its lead rule and lists three. One cell that has met the rule since the
first map, 30-day momentum on ranks 31–100, sat thirteenth and was never put through the hypothesis form. This
round does that, once.

**Choices that are not obvious.**

- *The book stands on the cell.* The map's own tier object with no added filter, the mapped variable with no day
  skipped, a weekly hold because the qualifying cell is the seven-day one. Each line of the definition says whether
  it is the cell's or not.
- *The form says the size is negative before costs.* The cell's rank correlation is negative while its top-minus-
  bottom mean runs the other way (+21 bp a week), as the first map already found for the volatility variables on
  this tier. The form's lines 1 and 2 are behavioural and are read "filled but weak", as H-D1's were. The prior is
  1%.
- *The null is round 17's, not round 9's.* H-R1's null redrew its variable every week, which matched a 7-day
  return. A 30-day return keeps its ranks; weekly-redrawn null books would lose on costs alone and the p would
  reward trading less. The null here is each coin's daily noise summed over 30 days, and counts check the
  book-to-null turnover ratio on inputs only.
- *Counts fill the cost before the measurement.* They print the book's weekly turnover and the cost it implies,
  from weights alone.
- *No four-week book.* The 28-day cell was seen and did not meet the rule; that variant is ruled out beforehand.

Also in this commit, by addition to §48: twelve cells meet the lead rule, not eleven (extreme days on the top
tier, tied to its sweep row), and §3.3's "rebuild every lead under each new logic" is superseded by §22.2.

**Counts (22:26Z, no return read).** No stop: 99.2% of ranked coin-decisions have funding on file, and each of 2022,
2023 and 2024 has a book at every decision. 2020 has a book at 4 of 52 decisions, since the tier barely existed;
the five-year reading counts those months as zero, as registered. The book's weekly turnover is 0.766 against the
null books' 0.722 (ratio 1.06, inside the band), so the p will be about what is picked. Turnover alone puts the cost
at 10.4% of total position a year at 26 bps; with the cell's own mean spread the form's size line now reads about
−16% a year after costs.

**Result (22:44Z): H-R2 fails and is closed.** Annual Sharpe −0.559 after costs, −12.1% of total position a year,
four losing years of five, p 0.445 beside 300 null books. All three registered falsifiers show: the entry rule is
missed, nothing is picked, and the book loses at the engine's cost too (−0.275).

- *The rank was right and the mean lost, in the book as on the map.* The cell's rank correlation was −0.06; the book
  built on that rank lost 1.7% a year on price before any cost, the direction the cell's own top-minus-bottom mean
  had pointed.
- *The null books lose too* (−8.9% a year on average): on this tier a weekly rank book costs that much with nothing
  in it.
- *The form's size line* said about −16% a year; the book made −12.1%. Sign and magnitude held; funding was not in
  the form.

With this, every one of the twelve cells that meet the map's lead rule is tied to a record.

**Added (23:05Z).** One reading corrected, the verdict unchanged: split by year from the two printed accountings,
the book's loss before costs is the four decisions of 2020 (−20.6). Over 2021–2024, the years the map cell has its
decisions, the book is about flat before costs (+1.1% a year, swinging −23, +39, −12, 0) and loses on cost. The
falsifier at the engine's cost still holds on those years.

Then one more row of the table (§50). The premium files came from the venue's public API, which serves listed
symbols only, so the premium was read on survivors: 44.5 coins a decision on ranks 31–100 against 53.3 for bar
variables. The archive holds monthly premium index klines for 1,010 symbols, delisted ones included. Filling them
is a reach round on a source already in use, so it is done without asking; the premium's readings are then read
again on the changed inputs. A sweep scope line is corrected too: the sweep read the top tier only.
