# 2026-11-10 — Variance premium v0.1: an index proxy as a necessary condition, pre-registered

**What.** `docs/proposals/VARIANCE_PREMIUM_V0.1.md` pre-registers stage 3a of `RESEARCH_DATA_SOURCES_V0.1.md`: a
30-day variance swap sold at DVOL and settled at the index's realized variance, 67 non-overlapping blocks from
2021-03-24, BTC and ETH averaged, no cost. It is option 5 of `CRYPTO_DESIGN_REVIEW_V0.1.md`.

Added:
- `scripts/research/variance_premium.py`.
- `tests/test_research_variance_premium.py`.

**Why a proxy first.** The tradable measurement needs years of expired-option trades, a collection of its own. The
DVOL history is already in the cache. An index proxy that charges nothing is close to an upper bound, as a judgement and not a theorem: a variance swap
and a hedged option sale have different payoffs, so a failed proxy makes a passing option structure unlikely, not
impossible. Thomas is asked (Q1) whether closing on that judgement is acceptable.

**Why a pass does not go to the screen.** DVOL cannot be traded and the spread in volatility points is not on
record here, so no cost is charged and none is invented. A pass earns the pre-registration of the tradable
measurement and nothing else.

**Why the forward-only observation is untouched.** `iv_rv_observe` refuses to value any day before 2026-11-10 so
that the past premium is not seen before a strategy is registered. This script is that registration. It is a
separate module; the observation's cutoff and its test stay as they are, and the first historical value is computed
only by the official run after the merge.

**Why blocks do not overlap.** Daily entries with a 30-day horizon would share 29 of 30 days and make 2,000
observations out of about 67. One entry every 30 days keeps the count honest and the Sharpe formula simple.
