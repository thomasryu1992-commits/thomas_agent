# 2026-11-16 — discovery loop round 49: the spot pair's trade flow

**Shipped (registration and the reader; nothing read in this commit):** `scripts/research/signal_lab.py`
(`attach_spot_flow`, `_spot_flow_window`, `hold_spot_flow`, two overlap candidates), a test,
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §67.

**Why.** Round 48's accounts ratio repeated the all-accounts ratio; the spot klines' count and taker-buy columns are
the other unread columns round 47 named.

**Choices that are not obvious.** The perp's trade-flow reader is reused on the spot files, and a day counts only
when S01's same-asset rule holds, so a reused ticker's other asset never enters a window. Two levels, not three: a
shape waits until its level has passed the overlap.
