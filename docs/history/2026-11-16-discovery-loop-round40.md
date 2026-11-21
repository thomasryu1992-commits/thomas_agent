# 2026-11-16 — discovery loop round 40: ranks 101 and after, read by the map and by the tier sweep

**Shipped (registration and tool; nothing run in this commit):** `scripts/research/signal_lab.py` (`LOWER_TIER_NAME`,
the tier's strikes in `SPREAD_NO_BOOK`, `grid(..., members=)`, `candidates(..., cap=)`),
`scripts/research/strategy_ladder.py` (`TierSweep(..., members=, name=)`, `_whole_span`, `lower_out`, `lower_names`,
`lower_refused_groups`, `lower_sweep_tier`, `lower_gate`, `lower_coverage`, `lower_tier_counts`, `tier_later`,
`lower_sweep`, `lower_sweep_reading`, `lower_map`, commands `lower-gate|lower-counts|lower-map|lower-sweep`), tests,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §57.

**Why.** Round 39 found the map's doors coarser than the entry rule, so its registered next step reads the tier
below rank 100 with both the map and a sweep. No reason was ever written for not reading that tier.

**Choices that are not obvious.**

- *One tier definition for both readers, and it is H-R1's.* The tier is `LowerTier` as coded (liquidity floor,
  funding on file, per decision day). The H-R1 book run through the new code path must reproduce `lower1.json`,
  and that is the gate. Fetching GTCUSDT funding now would move the membership and break the gate, so this round
  does not fetch it.
- *The tier is never added to `signal_lab.TIERS`.* Adding it would add random-variable cells and could raise the
  lines the two mapped tiers are read against. The tier gets its own lines from its own controls, and the
  existing paths are held by two reproductions (`tier-gate` against `mid1.json`, one map group against `run14`).
- *One struck list.* The map's `no_book` and the sweep's out-list read the same dict, and a test pins that.
- *Coverage is a stop here, not a note.* Positioning files were fetched for the higher tiers' member months, so a
  coin below rank 100 has a value mostly in the month before it joins the top hundred. That is selection on next
  month's rank. Positioning is struck by name. Every other variable must have a value on 80% of what a null book
  ranks, both overall and on the coins that are not in the top hundred the next month.
- *The later-years rule is inside the sweep's lead rule* (section 55.5). A year with fewer than 20 decisions before it
  names no side and counts as zero, as H-R1 counted a year with no book. It is never dropped.
- *Leads are listed without the cap of three*, each marked with whether a record strikes its variable. Otherwise
  three struck cells could hide one that is not struck.

## Result (2026-11-16 04:55Z): the sweep found nothing; the map found two leads

- All three gates reproduced their records to the digit. Spot was stopped by coverage (0.67). 24 books were built
  on ranks 101+, each over 40 months from 2021-09.
- **Sweep:** none of the 24 survived Benjamini-Hochberg and none cleared the entry rule. The smallest p was 0.151.
- **Map:** this tier's own lines are 3.64 (7 days) and 3.21 (28 days). Two cells of unstruck variables met the first
  rule: residual momentum 30 at 7 days (t −3.80) and listing age at 28 days (t +3.44, 41 decisions). A struck cell
  (momentum 30, H-R2's record) also met it and is only named. No cell met the second rule.
- Neither lead is a candidate. The sweep's books on the same two variables are already read and lose money after
  costs. Round 41 is their check round, as section 57.8 registered.
- Both leads' |t| sit under the two mapped tiers' lines (4.30/4.96). That is recorded as a fact and not used to
  re-judge anything: the registration used this tier's own lines.
