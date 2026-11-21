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

## Result (§98.5)

- *The recorded readings came back.* Upper: 30 values compared, 0 differ. Ranks 101+: 63 compared, 0 differ.
- *R1 passed on ranks 101+ only.* Coverage there is 1.0, and the nearest kin is range_30 at −0.650.
- *The upper tiers stopped at coverage:* 0.724 on the top 30 and 0.774 on ranks 31–100, against a floor of 0.80.
  - The cause is in the files. In the shared positioning file, one symbol's open interest starts in 2020-09 and 99
    start in 2021-12.
  - The registered rule (§98.1, "the first value of any symbol") therefore began the span on 2020-10-18, about
    62 decisions before most members have values.
  - Coverage was not recomputed on a later cut: that would be a new cut after the result.
  - §97.4's "(2022)" was wrong; the code and §98.1 agree, and the verdict follows them.
- *Every kin was compared,* except on ranks 101+, where 5 kin have no files: depth (D11) and hourly (§85.5).
- *The nearest record is the closed volatility cluster,* not OI growth, long-account share or funding level.
  - Range, vol, idiosyncratic vol and max are at −0.55 to −0.78 on all three tiers.
  - The top 30 passed the 0.8 overlap rule by 0.022.
- *One question to Thomas (§98.6, default no):* re-read the upper tiers' coverage once under the ranks 101+
  own-span rule?
- *Next (§98.7):* register R1 on ranks 101+ only. Keep it off the upper tiers in code, with a test. Make the
  returns-free 0.5 weight overlap against that tier's struck books the first door.
