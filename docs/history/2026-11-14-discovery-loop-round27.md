# 2026-11-14 — discovery loop round 27: the two new levels' shapes

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/signal_lab.py` (`LATE_LEVELS`;
the change and the position of `spread_30` and `premium_30` in the registry; 94 random variables),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §41.

**Why.** Round 26's item-by-item table: every registered 30-day level got a change (§33) and a position (§37)
unless a written strike covered it. No strike covers the two levels round 26 added, so they are owed.

**Choices that are not obvious.**

- *Undecided is not struck.* The spread might be kin of the closed volatility cluster, which would strike its shapes.
  Round 26 could not settle that (half its values are tied at zero), so the shapes are made.
- *This ends.* Shapes are made of 30-day levels only and a shape gets no shape; a test holds both. A new level adds
  five variables at most.
- *The premium's shapes load through the premium door*, like the level: the group name carries over from the level.

**Result (2026-11-14 17:20Z).** No lead among the four: the largest |t| is 2.99 (the premium's position, ranks
31–100, seven days, weeks of high volatility) against a line of 4.30. The two levels' shapes are finished. Since
round 18 the map has taken 22 new variables and shown no lead.
