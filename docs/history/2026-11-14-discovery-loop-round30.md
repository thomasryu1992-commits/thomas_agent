# 2026-11-14 — discovery loop round 30: the sweep on the variables added since round 6

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`sweep_market`, `sweep_matches`, `sweep_family`; the sweep and transfer commands load through `sweep_market`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §44.

**Why.** Round 6 asked which registered variables make a book, directly, and answered for the 27 there were.
The registry has 55 now. The null's size was raised four times "so the sweep can correct for every variable" and
the sweep was never run on the additions. Nothing written strikes it.

**Choices that are not obvious.**

- *One null for the whole family, no mixed p-values.* Round 6's p-values stand beside 300 null books; their floor
  (1/301) cannot reach the first threshold of a correction over 55 (0.10/55). So the 27 recorded Sharpes and the 28
  new ones are all read beside the same 600. Round 6's own verdict is left as written.
- *The recorded books are carried, the re-run ones must match.* Each re-run group rebuilds its old variables too.
  They must come out as recorded (side, Sharpe, return, decisions) and the first 300 null books must match by
  value, before any new row is read. `sweep_family` refuses otherwise.
- *A loader per group.* Round 6's combined load (positioning, depth, spot) no longer fits the sandbox, and a
  premium sweep through the bars-only door would rank nothing and still exit 0. An attached group is swept alone
  through its own door; a set with no loader is refused. Depth has no new variable and is not rebuilt.
- *Short books are read conservatively.* A month with no book counts as zero, so a variable with values from 2023
  only is at a disadvantage beside five-year null books. Decisions ranked are printed beside every row.
- *p is rounded to four places*, as round 6's reading did; the function is not changed.

Also by addition to §43: the refusal-tally tool (#1475) was merged on 2026-11-14 (`3143dfea`).
