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
