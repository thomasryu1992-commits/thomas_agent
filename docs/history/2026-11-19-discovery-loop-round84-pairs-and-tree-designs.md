# 2026-11-19 — discovery loop round 84: two other ways to find a strategy (designs)

**Shipped:** `docs/proposals/PAIRS_RELATIVE_VALUE_V0.1.md` (L26), `docs/proposals/NONLINEAR_MODEL_SEARCH_V0.1.md`
(L27), and `STRATEGY_DISCOVERY_LOOP_V0.1.md` §102 with the status line and a map row. Document only; nothing computed.

**Why.** Thomas asked to find strategies another way. He picked both options offered, pairs relative value and a
nonlinear model search, and accepted the shared defaults in the question: daily bars on disk, 2020–2024, the 15% and
Sharpe 1.0 bars, pre-registered, measured once. His decision opens the two logics, not round 83's paper table.

**Choices that are not obvious.**
- *Pairs on the top 30.* Both legs must be held, and the top tier is the most liquid. It is the noisiest tier for a
  null (7.7% of 4-week null books clear stage 2(a)), so the prior is low (5%).
- *The pairs null keeps the trading rule and draws the pairs at random.* The real book beats it only if picking
  pairs by distance adds something. A plain short-horizon reversal would show up in the null too.
- *The planted pair is synthetic.* One leg's daily return is replaced by the other's plus a mean-reverting gap with
  a known half-life. That measures what the door can see before the real book is read.
- *The tree model sees daily-bar variables only (11 on ranks 31–100), never a struck name.* Round 81 showed that a
  candidate built near the volatility cluster rebuilds it. Its book also gets the 0.5 weight-overlap check against
  every struck book before any return.
- *The tree model's null runs the whole pipeline on random variables.* That covers the walk-forward and every fit.
  How many null books there are is set by a first timing in pure Python.
- *Two questions to Thomas, for L27 only (defaults yes):* keep the inputs to daily bars, and apply L24's S1
  exception, under which the form is filled after a model picks the combination.
- *Block clock.* A new logic is §4's row 2, so D2 restarts the clock when its result is recorded.
