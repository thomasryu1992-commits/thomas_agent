# 2026-11-13 — discovery loop round 13: what the ladder asks for, in the ladder's own unit

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`read_daily`, `squeeze`, `read_squeezed`, `moved_to`, `bar_curve`, `bar_books`, the command `bar`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §27.

**Why a tool round.** Five hypotheses in a row failed at the entry rule, and round 12's table found no new
hypothesis with a structural payer left on disk, only three kin of closed records. The table in §27.1 is plain
arithmetic on numbers the earlier rounds printed: net of costs the five books made between -10.4% and +6.6% of
total position a year against a floor of 15%, and the kin of the three remaining variants are in that table too.
A run is not needed to say they are under the floor.

**What the run adds, and only that.** The floor is half of one of four conditions. With five years of noise in,
how far above 15% net does a book have to be before it passes all four? Each shape's null books are moved to a
net return, in % of total position a year, and read through the ladder; the bar is where half, and four fifths,
of them pass.

**Choices that are not obvious.**

- *The unit is net % of total position, the one the form's size line already uses.* A first draft mixed a known
  share of future returns into a random variable and read the pass rate against its rank correlation; the
  hypotheses were tail and event books judged in % of total position, and a bar in another unit could not be set
  beside them. A second draft added an edge on top of the null books, which measures "edge over a placebo" and
  equals nothing printed before unless placebo books average zero before costs; rounds 11 and 12 show they do
  not. Moving each null book to a stated net return removes both problems and needs no forward-looking variable.
- *The null books are the ones each round already used for its shape*, so the noise is the shape's own.
- *The move lands only on days the book has a row.* A book that is flat most of a year gets little of it that
  year, which is what the later-years condition would see in a real event book.
- *The bar is a grid point, not an interpolated value.* The floor is a step at 15; a line across it would report
  a bar under the floor.
- *What the result changes is fixed before the run.* The form's size line is set against the bar, the prior is
  capped by the curve, and any ask for a new source carries it. Whether to run the three remaining variants is
  not decided by the run: the arithmetic already has them under the floor, the form lets an under-floor
  hypothesis go up with a note, and keeping the loop running is Thomas's word, so that goes to him as one
  question.
- *A steady return is the best case*, so the bar can only say "too small".
- *Books are squeezed to monthly and yearly sums at once.* Three hundred daily series do not fit beside the market
  in the sandbox. `read_daily` repeats what `measure` computes after a simulation, and tests hold `measure`,
  `read_daily` and the squeezed reading to the same numbers.

## The result (same day)

**What came out.** Moved to a steady net return, half of a shape's null books pass all four conditions at 20% of
total position a year for the rank books on the top thirty (13% and 6% of them pass at the 15% floor itself), at
25% for the funding-tail shape and at 30% for the rotation shape. In every shape the share passing everything
tracks the share clearing Sharpe 1.0: with a steady return the ladder in effect asks for a net return above the
book's yearly volatility, and the floor, the later years, the null and stability are met before that.

**Set beside the five hypotheses.** Their net returns were -10.4% to +6.6%. On its shape's curve each sits where
no null book passes. The kin of the three variants still unread sit there too.

**Where the method broke its own assumption.** The crash shape's bar came out at the floor. Placebo hours are
kept clear of crashes, so their books are calm: about 9% a year on total position, against about 15% for the
real event book. "The noise is the null's" does not hold for that shape, and the record says the number is a
lower bound and nothing more.

**The rule as fixed, applied as fixed.** The rank shapes' bar is exactly five points above the floor and the
reading table said "more than five", so the form uses the floor for them, with the pass rates at the floor
written beside it. The prior line reads the curve either way.

**What was added, and one registered action corrected.** One line in the form: the size line is set against the
floor and the nearest shape's bar. The registration also said the prior may not exceed the curve's pass rate at
the stated size, calling that rate an upper bound. It is not. The method pins each null book's realized
five-year net return to the target, so the curve is the pass rate given a *realized* net return; a prior is about
a book *expected* to be that size, whose realized return spreads around it. Used as a cap it would set every
under-floor hypothesis to zero, which is only the floor's step again, and the five size lines written so far
missed by factors of two to ten. The curve's rate is written beside the prior, not over it. The registered
sentence stays on the page with the correction under it.

**What was measured, said exactly.** With the mean pinned, what the run measures is the null books' volatility and
its pattern across years, not five years of noise in the mean. A longer span would not lower these lines.

Whether to run the three remaining variants was not decided here; it goes to Thomas as one question, with their
kin's sizes. At the kin's sizes no null book passes, which is the arithmetic of the floor again; the variants'
own sizes are not known.

