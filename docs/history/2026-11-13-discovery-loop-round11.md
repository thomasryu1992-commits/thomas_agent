# 2026-11-13 — discovery loop round 11: a book that is paid to hold what others pay to short

**Shipped (registration and tools; nothing measured in this commit):** `scripts/research/strategy_ladder.py`
(`DeepFunding`, the book `L15` `deep_book`, `deep_counts`, `deep_legs`, `deep_run`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §25.

**Why a round at all.** The stall order's first four steps have been walked once and the fifth, a new source, is
Thomas's decision. The loop does not park on him: the next hypothesis the data on disk can carry goes up, form first.

**Why this hypothesis, and what its own form says against it.** A perp whose funding stays deeply negative has a
payer who can be named: holders hedging spot they cannot sell, and shorts who cannot borrow the coin. The book is
long those coins and short Bitcoin against their beta. The form's size line is written as two lines because they
are two different claims. The funding is arithmetic: at the threshold it leaves 4 to 8.5% of total position a year
after costs even with the book always full, under the 15% floor. The price leg has no payer in the form at all;
if the people paying the funding are right, it is negative. So the prior is 3%, and a pass could only come from
the leg nobody argued for.

**Choices that are not obvious.**

- *Two placebos.* Coins with deep negative funding are plausibly the recently beaten-down, volatile ones. The
  ladder's null replaces each pick with another pool coin for as long as the pick is held; a second null draws
  that coin among the ten nearest in trailing return. A book that beats the first and not the second held
  "beaten-down coins", which is H-R1's closed family, and is read that way.
- *What a pass would buy is fixed before the measurement, and it is not a confirm read.* The hypothesis is kin to
  C002 and C007, whose confirm results are known and were both lost to funding, and the idea was written after
  them. By the loop's own rule on relatives the confirm span is not independent evidence here.
- *The split is printed, not backed out.* Round 10's record had to subtract two printed sums to find that most of
  the judged book's price return was its Bitcoin leg. Here the same book is measured with and without the leg and
  each leg's price, funding and cost are in the output.
- *A pass on price is named apart from a pass on carry.* If funding is under half of what the book earned before
  costs, the result is recorded as a lead without a stated cause.
- *The threshold is a per-day sum over seven days*, so a coin whose settlement interval the exchange shortened
  does not cross it mechanically.
- *The book is allowed to be mostly empty.* A fifth of the long side per name, idle capacity at zero: a tail
  condition that holds two coins is measured as the two-coin book it is.

## The result (same day)

**What came out.** H-F1 failed and is closed. Over 2020-2024 the book's annual Sharpe after stressed costs is 0.123
against an entry rule of 1.0, 4.0% of total position a year, with 2023 and 2024 negative (-19.0%, -30.2%). It is
the first of the four hypotheses to end above zero after costs, and that is not counted as a lead.

**What the split showed.** The output carries each leg, so nothing had to be backed out. Funding received less
costs is about 13% of total position a year; price net of the beta hedge takes about 9% of it back. The payers
were about half right: the coins they paid to short rose less than their beta said. The form's funding line was
too low by a factor of 2.5 to 5, and the note written after the counts, that it would be lower still, was wrong:
it read the median one-week spell where the sum is set by the deep spells that last.

**What the two placebos showed.** Against other pool coins held for the same spells, 9 of 300 did as well (p
0.033). Against coins as beaten down, 20 of 300 did (p 0.070). The second null's mean is higher than the first's:
holding beaten-down coins already buys part of the gap. The registered reading of that is that funding was not
told apart from H-R1's closed family. The first p passing alone counts for nothing, as the later-years condition
did for H-L1.

**What is not done with it.** No second threshold, window, cap or hold rule; no re-measurement with another beta.
Had it passed, the confirm span would not have been asked for: the idea is kin to C002 and C007 and came after
their reads.

**An order note.** The registration commit's own run lost one check (windows pytest) to the next push, which
superseded it; the head with the same code passed all five, the last finishing while the measurement was already
running.

