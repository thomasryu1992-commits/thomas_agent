# 2026-11-16 — discovery loop round 46: spot on ranks 101+, from a fetch of its own

**Shipped (registration and tool; nothing fetched or read in this commit):** `scripts/research/strategy_ladder.py`
(`LOWER_SPOT_*`, `_spot_months`, `lower_spot_plan`, `lower_spot_fetch`, `shared_spot_digest`, `lower_spot_seal`,
`lower_spot_market`, `lower_spot_agree`, `lower_sweep_family`, commands `lower-spot-plan|digest|fetch|seal`,
`lower-family`, `--only spot` read alone), tests, `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §64.

**Why.** After round 45, the one table row left whose reason was a cost: the spot group on ranks 101+. Round 40
stopped it at the coverage floor (0.67) before any return was read.

**Choices that are not obvious.**

- *A root of its own.* The forward panel's S01 reads and fetches into `archive-spot/`, and its fetch rewrites
  `derived/spot-share/served.json` with its own pairs. The seal requires both to hash as before.
- *"Not served" needs a listing that succeeded.* The listing is written before anything is fetched. A listing error
  stops the fetch and is never recorded as a month the archive does not serve. A pair the shared served list knows
  must list at least those months again.
- *Both roots' bars are attached.* They are the same archive files; a gate counts the overlapping pair-days and
  requires them equal, and fails when the overlap is empty.
- *The family is assembled by a command.* Round 40's and round 45's sweep files are kept as recorded. A new book may
  fill only a row recorded as skipped (the three spot rows), and the recorded books must give round 45's recorded
  reading before anything is added.
- *Two lessons from round 45 are written before the read.* The threshold is stated as the function computes it, for
  any number of new books. Months without a book count as flat, while the null books stand, and that does not change
  the reading.
