# 2026-11-10 — Premium convergence v0.1: open a spot-long / perp-short when the premium beats the round trip, pre-registered

**What.** The third screen candidate under `STRATEGY_SCREEN_GATE_V0.1`, after funding basis (live) and the
quarterly carry (ENTRY_FAIL, #1413). `docs/proposals/PREMIUM_CONVERGENCE_V0.1.md` pre-registers the
long-spot-only arbitrage of He, Manela, Ross and von Wachter (*Fundamentals of Perpetual Futures*, §4.3) on six
USD-M perps' 1h bars. It is screened only if its historical annual Sharpe is at least 1.0.

Added:
- `scripts/research/premium_convergence.py`.
- `tests/test_research_premium_convergence.py`.
- A READ row for the script in `tests/test_mvp_runtime_crypto_script_roster.py`.

**Why the threshold is the cost model, not the paper's fee tiers.** The paper's dearest tier is a maker fee (a
round trip near 0.164%); the repository's model is taker plus slippage (0.42%). The cost model is shared with
every other candidate, so it stays, and the threshold is derived from it. A test raises the spot fee and the same
premium stops opening. The proposal records that this makes the paper's Sharpe an upper bound, not an estimate.

**Why idle days count as zero.** Every candidate under the gate is judged on capital over the whole span. The
paper's Sharpe counts only open periods; the two are not comparable, and the proposal says so before the run.

**Why a missing premium hour is bridged and a missing index hour blocks.** The premium only decides; the index
and mark price the position. One market-wide premium hour is missing (2020-12-01 23h), so skipping a decision
there costs nothing, while pricing a held position across a hole would be imputation.

**Why the price data was not read before the rules.** The load step prints dates, counts and a digest only (a test
asserts no price or premium reaches its output). No return, Sharpe or premium value was computed before
registration.
