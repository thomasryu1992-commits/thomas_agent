# 2026-11-11 — the momentum basket screen's funding coverage, amended before its window opens

`scripts/research/momentum_basket_screen.py`, `docs/proposals/MOMENTUM_BASKET_V0.1.md` ("수정").

**Why a registered screen is touched at all.** Its coverage rule asked for a funding settlement on every day a
universe member has a bar. A delisted coin keeps flat zero-volume bars while its funding files stop, so one delisting
inside the window (2027-02..05) would have held the screen at NOT_YET for good. Applied to the real archive, the old
rule sticks on each of three past four-month windows (HIFI, DEGO, TON, VANRY). The fix was written for the metrics
basket screen the same day; leaving two rules would have meant two answers to the same archive fact.

**Why now.** The alternative is to meet the NOT_YET in 2027 and choose a rule then, when the window's returns are
one command away. A coverage rule chosen before the window opens cannot be chosen for its result.

**What the amendment is allowed to change.** Coverage only. The baskets, sizing, costs, window and verdict rule are
untouched, and a test pins the judged sum to a direct engine run. One part is stricter than before: a leg decided in
one month is held up to a week into the next, so that week's funding is now required too.

**One owner.** `funding_shortfall` lives in the momentum screen; the metrics basket screen calls it.
