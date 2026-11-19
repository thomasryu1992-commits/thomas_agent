# 2026-11-19 — discovery loop round 80: the leverage stay's input-only doors (registration)

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §98, the status line, and the code it names.
- `signal_lab`: `R1`, `OI_TURNOVER`, `leverage_kin`, `first_value_day`, `tier_overlap`, `tier_persistence`.
- `hourly_vars.coverage` takes another reading (`read=`).
- `strategy_ladder`: `leverage_market`, `leverage_reproduce`, `leverage_doors`, and the command
  `leverage-doors --tiers upper|lower --check FILE`.
- Tests in `tests/test_research_strategy_ladder.py`.

No value is read in this commit. The doors read no return.

**Why.** §97 raised one variable, the leverage stay (open interest × close ÷ perp quote volume, 30 days). §97.5
corrected where it reaches and how its kin are built before any value existed.

**Choices that are not obvious.**

- *A recorded reading comes back first, on the same market.*
  - Upper tiers: round 68's price-delay coverage and overlap.
  - Ranks 101+: round 45's positioning coverage.
  - The upper market joins the hourly values, positioning values and trade flow for the first time. The lower run
    reuses a loader that no new variable has been read through yet. A synthetic market could not exercise the lower
    tier's membership, so the record is the check.
  - If anything differs, R1 is not read and the run exits 2.
- *R1 stays out of the registry until its doors pass,* as the two hourly variables did (§86 → §87).
- *The overlap is read per tier, each tier against its own struck names (`no_book`).* §40 read the top tier only. On
  ranks 31–100 the kin now include funding level, OI growth and long-account share.
- *A kin list is never shortened to fit memory.* If the upper run is killed by the 700M cap, the round stops and a
  split of the market is registered separately.
- *Two runs, upper then lower.* The two markets load different positioning files, and the lower tier's files are
  never mixed with the shared ones (§63).
