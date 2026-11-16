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

## Result (2026-11-16 23:33Z)

Read once in the registered order, all exit 0 (§67.2). The old lines reproduced from the first 106 controls, and
all three families gave their recorded readings back. On the top tier, the spot trade size's 7-day all-weeks cell
cleared the map rule (t +4.50 against the new line 4.34), the first map lead since round 40; its own book in the top tier's sweep (L9T, held four weeks)
read in the same round is under the entry rule (Sharpe 0.714, p 0.21). On ranks 31–100 the spot taker-buy share
survived the correction as the step-up's second rank (p 2/601, behind the recorded funding_30), under the entry rule
(0.900), so it is named and nothing is built. Ranks 101+ have nothing. No candidate; the next round is the lead's
check.

**Correction (2026-11-16 23:43Z).** The result above first called that book weekly. The top tier's sweep holds each
decision four weeks (§67.3), so the lead's own 7-day book has not been read.
