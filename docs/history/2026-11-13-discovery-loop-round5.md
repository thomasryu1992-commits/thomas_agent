# 2026-11-13 — the discovery loop's fifth round: the universe without its tail, and a lead that does not become a book

`scripts/research/strategy_ladder.py` (`without_tail`, `tail_out`, logic `L9T`), `scripts/research/signal_lab.py`
(`RESTING_AXES`), `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §18.

**What it is for.** The universe's character had just shown that 2026 — the year both confirm reads were lost — is
unlike any development year on three counts: the tail of funding, the share of coins moving over 50% in a day, and
membership turnover, while the median coin was quieter than ever. If a handful of extreme coins is why development
passes did not carry over, a book that leaves them out should say so.

**The tail-out logic.** At each decision a coin is left out of the tier when it moved 50% or more in a day within
the last 30, or when the size of its last week's funding is above the tier's 90th percentile that day. The second
half always removes about three of thirty: on a calm day they are merely the three with the most funding.

**The five scenario axes rest.** Two checks in a row — the average over variables, then one variable at a time with
enough placebos to be passable — found none that beats spells placed anywhere. Their cells stay on the map and are
no longer leads.

**What came out.** Taker-buy share, a lead on the map above every random variable, makes a book of 0.57 that six of
twenty random-variable books match; most of what it earns is funding. Of the map's three leads only volatility has
produced a book past the entry rule, and only after risk-matching and a funding filter: a large rank correlation and
a book that earns are far apart. The low-volatility book without the tail reads 1.210 with no losing year, above the
same book with it (1.051). It is the seventh build of that lead, one random book in twenty is above it, and the tail
was defined after looking at 2026.

**What the development years cannot say.** They hold almost no tail, so they cannot tell whether leaving it out
would have saved 2026. The confirm span can — as a diagnosis of the cause, since this book differs from one already
read there only by the tail — and whether to spend the third of five reads on that is asked.

## Confirm read 3 and the book sweep (same day)

**Confirm read 3 (D8, Thomas 2026-11-13).** The low-volatility book without the tail (C010) read an annual Sharpe of
0.138 on 2025-01..2026-09 against a bar of 1.388: CONFIRM_FAIL. 2025 was +20.3% and 2026 was -14.6% of the position
total, against +15.3% and -11.9% for the same book with the tail. The reading of each outcome had been fixed before
the read: failing with 2026 still negative closes C010 and ends book-building on the volatility lead. Leaving the
tail out more than halved what the book paid in funding (19.7% to 8.3% over the span) and did not help 2026. Eleven
of twenty random-variable books of the same logic did as well. Three of five reads are spent; none passed.

**The sweep (round 6).** Every one of the 27 registered variables was built into the same book (the tail-out logic,
four-week hold), with the direction chosen by the sign of its own rank correlation, beside 300 random variables
built the same way. No variable survives Benjamini-Hochberg at 0.10; two are under 0.05 where chance gives 1.35.
The four that clear the entry rule are one cluster (volatility, idiosyncratic volatility, largest daily return,
skew). **7.7% of the random books clear the entry rule**, and their mean is 0.35: the logic earns on its own in the
development years, so an absolute Sharpe of 1.0 does not separate a variable from noise under it.

**Order of events, for the record.** The read's registration was pushed at 06:21Z, the sweep's reading was seen at
06:33Z and the read was taken at 06:35Z. The registration and its reading table were not touched in between.

**What follows.** The three confirm failures now have a simpler reading than "2026 was different": the development
passes were not distinguishable from chance to begin with. The next change is to how the ladder decides a pass, and
the question it starts from can be measured inside 2020-2024 without touching the confirm span: does a book that
passes on earlier years earn in the later ones?

## The transfer check (round 7, same day)

**What it is for.** Three development passes had failed on the confirm span, and the sweep had shown the passes were
not far from what random variables give. Two readings were left standing: 2026 was unlike the years before it, or
ranking books by five years of Sharpe never said anything about the next year. The second can be measured without
the confirm span, so it was: `strategy_ladder transfer` picks each book's side from the earlier years only (2020-21,
2020-22, 2020-23) and reads that same book on the year after, for the 27 variables beside 300 random ones. The
reading of each outcome was written down before the run.

**What came out.** Across books, the earlier years' Sharpe and the next year's are unrelated: a rank correlation of
-0.014 averaged over the three splits, where sets of 27 random books give 0.02 on average (p 0.62). By the table
fixed in advance that is the "otherwise" row. It is a carry that was not seen, not one shown absent: with 19 to 27
correlated books a split, only a rank correlation above about 0.22 could have passed. Beside it, deciding nothing: the cells that had cleared the entry rule
earned 16.3% the year after against 2.5% for random books as a whole, and that average is two groups — the twelve
cells of the volatility cluster, all positive, and six others (momentum, beta, funding) that lost on average. Five
variables changed the side they are long between splits.

**What follows.** The ladder's rungs 2 and 3 judged a book by its Sharpe over the five years it was chosen on. The
next change makes them walk forward — side and book from the earlier years, value from the year after, read beside
random books of the same logic — and moves where candidates come from: from whatever ranks well on the map to
things that have a reason before the data is looked at.

