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
