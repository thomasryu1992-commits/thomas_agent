# 2026-11-10 — Momentum basket v0.1: the one consistent result in the failure record, resized and sent to a forward screen

**What.** After positioning and the variance premium both failed that day, Thomas asked for a new strategy built
from the failure record. `docs/proposals/MOMENTUM_BASKET_V0.1.md` pre-registers the 30-day cross-sectional momentum
basket (`S-MOM30`, top 30 coin perps), sized by inverse volatility inside each side, for a forward screen over
2027-02-01..2027-05-31.

Added:
- `scripts/research/momentum_basket_screen.py`.
- `tests/test_research_momentum_basket_screen.py`.
- `cross_section_basket.simulate` takes an optional `weigher`; its default is the behaviour every earlier
  measurement ran under.

**Why this rule.** It is the only measurement in the record whose sign held everywhere it was read: a Holm-passing
LEAD on 339 futures weeks, and a positive mean in both years of an untouched spot holdout that still missed its
p value. With the futures-period effect as the truth, 96 weeks would have confirmed it about half the time, so the
holdout failed on sample size at least as much as on the effect.

**Why it is an exception.** The screen gate admits a hypothesis only at a historical annual Sharpe of 1.0, and the
unbiased estimate here is about 0.6; that document names `S-MOM30` as not a candidate. Thomas admitted it anyway
after seeing that, for this strategy only. The proposal records the exception instead of re-deriving an entry
figure from data that has already been read.

**Why nothing is re-run on the past.** Both spans were read once. A resized basket measured on them again would be
the same data under a changed definition. The script reads no pre-window day as a result, and the verdict is the
window's sign.

**Why only the sizing changes, and no volatility target.** The record argues for tail control: the equal-weight
basket's weekly standard deviation is near ten percent. (The holdout's two forced closes were delisted legs, not
volatility events, and are not the reason.) Inverse-volatility weights use the signal's
own 30 days, so no new window is chosen. A portfolio-level target would add a number to pick and cannot change the
sign the screen judges; position size is decided if the screen keeps it.

**Why coverage is checked first.** An unfetched month reads as delisted coins and unpaid funding. The screen
reports `NOT_YET` with symbols and dates only, and an archive-wide hole is `BLOCKED` because the basket would read
it as every leg ending.
