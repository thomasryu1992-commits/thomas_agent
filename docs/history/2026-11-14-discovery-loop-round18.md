# 2026-11-14 — discovery loop round 18: the weekly and monthly checks by hand, on the cells the new files fill

**Shipped (registration only; no code, nothing run in this commit):**
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §32.

**Why now.** Round 17 closed H-M1 and left the set-aside table empty: nine hypotheses measured and missed, one
stopped at its counts. Thomas 2026-11-14, to the question whether to merge #1474: "병합 없이 다음". What "next" is
follows his standing word of 2026-11-13, to keep running the weekly and monthly checks by hand until a result is
meaningful. Round 17's fetch made one of those checks possible: the map's positioning cells on ranks 31-100, which
were empty (two or three coins a decision).

**Choices that are not obvious.**

- *A tool round with no code.* `signal_lab run --only positioning` already maps the group on both tiers beside
  the random variables. Registering it is writing down, before the run, what is read and what a lead may and may
  not become.
- *No book in this round, and two of the five variables can never become one here.* Open-interest growth and the
  all-account long share on ranks 31-100 were read as a book an hour earlier and did not stand apart from noise.
  A lead in their cells would be H-M1 read again in another shape. The other three were never read on this tier.
- *The top-30 cells are not new evidence.* They were seen in the first map and have since been refilled for M04.
- *A run killed by the memory cap is not a measurement.* The derived positioning values more than doubled; the
  cap stays, and the fallback is to load only what the group reads and run once more.

**Results (same day).**

- *The first run was killed at the memory cap and is not a measurement.* It died while loading, with no cell
  computed: a positioning-only map still loaded the depth and spot series. `signal_lab.hold_positioning` now loads
  what the group reads; the cap stays. One run followed, after the fix's checks.
- *No lead.* The largest |t| among the positioning cells is 3.52; the random variables reached 4.30 (7 days) and
  4.75 (28 days). The cells on ranks 31-100 hold values for the first time, 88 of them.
- *Open-interest growth ranks the same way on both tiers and is under the line on both*; the book built on it an
  hour earlier lost before costs. The registration already said no lead in its cells would become a book.
- *The one-page report is in the document*: nothing alive past rung 3, reorganisation steps 1 to 4 done, step 5 (a
  new source) answered no on 2026-11-12 and not asked again. What the cycle has left on the data in hand is the
  monthly pair: more shapes of registered variables beside random books, and one new scenario axis.
