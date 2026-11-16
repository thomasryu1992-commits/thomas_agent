# 2026-11-16 — discovery loop round 49: the spot pair's trade flow

**Shipped (registration and the reader; nothing read in this commit):** `scripts/research/signal_lab.py`
(`attach_spot_flow`, `_spot_flow_window`, `hold_spot_flow`, two overlap candidates), a test,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §67.

**Why.** Round 48's accounts ratio repeated the all-accounts ratio; the spot klines' count and taker-buy columns are
the other unread columns round 47 named.

**Choices that are not obvious.** The perp's trade-flow reader is reused on the spot files, and a day counts only
when S01's same-asset rule holds, so a reused ticker's other asset never enters a window. Two levels, not three: a
shape waits until its level has passed the overlap.

## Overlap and registration (2026-11-16 23:11Z)

Both passed the overlap on inputs: 0.34 and 0.72 with the perp's own flow on the top tier, under the 0.8 rule. The
registry goes to 57 variables and the map controls to 110, with the first 106 unchanged by name. Each tier gets a
way to load the group, and a `family` command reads a tier's recorded rows again by its own function and must give
the recorded reading back before a new book joins. Run on this tree with no new book, all three tiers reproduced.
