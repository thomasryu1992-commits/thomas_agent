# 2026-11-14 — discovery loop round 33: the per-variable axis check on the variables added since round 4

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py`
(`axis_by_variable` keeps each test's counts, the counts at round 4's 5,000 placebos and the names of dropped
tests; `round4_matches`; `axes_family`; `hold_depth` and its dispatch),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §47.

**Why.** Round 4 asked whether an axis matters for two or three variables only, and answered for five axes and 27
variables. The map has six axes and 55 variables now. This is the last row of the table re-read at the end of §42.

**Choices that are not obvious.**

- *An exact gate against round 4 at no extra compute.* Placebos are drawn in order from fixed seeds, so the counts
  after the first 5,000 are what round 4 saw. The run keeps them, and the p they give must equal round 4's
  recorded p for all 230 tests before any new row is read.
- *The family is read from counts.* A run prints p rounded to five places, which is about 4% of the smallest p
  here; and a run's own survivor list corrects over that group only. `axes_family` rebuilds exact p-values from
  the counts of all eight runs and refuses mixed placebo counts or a test seen twice.
- *One placebo count for every test.* Round 4's p-values stood beside 5,000 placebos and are not mixed in; its
  verdict is left as written.
- *The thirteen axes that are not on the map are not checked by variable, and the registration says what that
  leaves unseen.* Their registrations made the average check the door to the map and said a failing axis is not
  re-checked; a second kind of check would reopen that door.
- *Depth gets a loader of its own.* The combined load is the one round 18 was killed on.
- *A thin test is named.* Round 4 dropped a test whose placebos mostly had no separation without a trace.

**The gate stopped the chain at the first group (20:44Z); nothing was read.** Ten of the funding group's twenty
tests shared with round 4 came out different, all on ranks 31–100. The cause is an input, not the code: round 8
fetched funding files for 158 more symbols after round 4 had run, so the coins that tier ranks by funding went from
46.4 to 52.7 a decision; the top tier is unchanged at 27.1. All 46 of round 4's variable-tiers were compared on
coverage before and now, and those two are the only ones that moved.

- *The registered gate was too strong.* "All 230 must reproduce" assumed nothing on disk had changed since round 4.
  It is amended by addition before any other group runs: the two changed variable-tiers are named from the inputs,
  their ten tests are new tests, and the other 220 must still match to the digit. The funding run is kept, not
  re-run; `scripts/` is unchanged by the amendment.
- *What it says about the record.* Round 4's funding tests on ranks 31–100, and the first maps' funding cells on
  that tier, were read on the 330-symbol funding set that round 8's own registration called a lookahead subset.
  Round 4's verdict is not changed.

**D10 answered the same day: "올리지 않음".** The lead does not go on the ladder. It stopped at the form's door, not
at a closing rule; it stays on record and is not re-read under any other definition.

**Result (2026-11-14 21:34Z).** All eight groups pass the amended gate: round 4's 230 tests are all present, the 220
on unchanged inputs match to the digit, and the only differences are the ten named beforehand. Of 642 tests none
survives the correction; 37 are under 0.05 where chance gives 32.1. The nearest is the one round 4 had nearest,
30-day funding across the funding-level axis (p 0.00069 against a first threshold of 0.00016), and the book built
on that combination lost the confirm span. The breadth axis, seen by variable for the first time, has nothing
near its threshold.
