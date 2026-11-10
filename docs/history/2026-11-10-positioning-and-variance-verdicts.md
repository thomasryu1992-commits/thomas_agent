# 2026-11-10 — Two pre-registered measurements, two failures: positioning ENTRY_FAIL, variance premium PROXY_FAIL

**What.** Both measurements registered that morning were run once, each from the merged SHA of its own
pre-registration, and both missed the floor of annual Sharpe 1.0.

- `POSITIONING_DIVERGENCE_V0.1`: −0.993 over 45 months, 6,063 trades, mean −0.046R. Both families lose. Closed;
  `positioning_store.MINTING_DECIDED` is not asked and stays False.
- `VARIANCE_PREMIUM_V0.1`: 0.778 over 67 blocks with no cost charged. Closed; stage 3b (option trades) is not built.

**Why the splits are recorded and not acted on.** SOLUSDT alone is positive in the positioning run, and BTC alone
and the pre-2024 half clear 1.0 in the variance run. Each is one cut out of several, seen after the fact. Both
proposals fixed "the same data is not judged again under a changed definition" before the run, so the cuts are shown
and nothing is re-run on them.

**Why the worst variance block was checked before it was recorded.** One block (entered 2026-01-27) lost 74
volatility points, about sixteen blocks' average gain. A single bad close would produce the same number. The two
currencies' index closes agree on a −15% and −16% day on 2026-02-05, so it is a market event and the verdict
stands.

**What this leaves.** The screen candidates are the two already registered (funding basis, premium convergence).
A new direction is Thomas's decision; none is proposed here.
