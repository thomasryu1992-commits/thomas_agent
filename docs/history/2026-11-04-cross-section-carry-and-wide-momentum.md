# 2026-11-04 — Cross-section test v0.2: funding carry and top-30 momentum, one pre-registration

**What.** `scripts/research/cross_section_basket.py` gains `--test v0.1|v0.2`. Each test fixes its universe size,
its signals and the earlier tests' p values in `TESTS`. v0.2 is the top 30, `S-CARRY` (7-day settled funding,
lowest long, highest short, weekly) and `S-MOM30`. Pre-registration:
`docs/proposals/CROSS_SECTION_CARRY_AND_WIDE_MOMENTUM_V0.1.md`.

**Why both in one test.** v0.1 came back NULL on all three signals. Both v0.2 signals were chosen after seeing
that result: carry from S-MOM30's funding share, and the top-30 momentum as a retest with more names a side. Both
need the same data, so a second signal costs only one more member in the Holm family.

**Why the family carries v0.1's p values.** The archive has now been read once. Putting v0.1's three p values
into v0.2's Holm family (five tests at α 0.10) is the price of reading it again. A later test carries all five.
v0.1's own readings are recorded and do not move.

**Display only, fixed before the run.** The weekly mean without funding (for carry it separates the price leg
from the payoff), and the weekly correlation of the two signals (is carry momentum renamed?).

**Default stays v0.1** so the recorded official run (`20261104T070402Z-164942`) keeps its meaning.

**Tests.** 9 added. Eight mutants are caught: both window shifts, carry direction, prior p values dropped,
ex-funding line ignored, v0.2 size 10, score read as return, unranked members not counted.
