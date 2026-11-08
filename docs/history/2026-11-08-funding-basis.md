# 2026-11-08 — Funding basis: could a one-month forward test confirm a spot-long / perp-short carry?

**What.** Thomas set a gate on 2026-11-08: a new strategy hypothesis is built only if its forward verdict takes
under a month. At 80% power and one-sided α 0.05 that needs an annual Sharpe near 8.6. No directional signal
measured here comes close; the best, S-MOM30, is about 1.2. The one structure the cached data can test against
the gate is the delta-neutral funding basis. `docs/proposals/FUNDING_BASIS_V0.1.md` pre-registers it.

Added:
- `scripts/research/funding_basis.py`: the measurement.
- `tests/test_research_funding_basis.py`: 15 tests on synthetic data.

**Why the gate replays a forward protocol instead of thresholding a Sharpe.** Thomas's question is how long a
forward test takes. The gate therefore runs that test, one calendar month of daily returns, on every past month
and counts how often it confirms. Funding returns are strongly autocorrelated day to day. A plain daily t would
overstate them, so the month's p comes from a circular block bootstrap. The monthly Sharpe is shown beside the
gate as its normal-approximation equivalent and is never judged.

**Why the spot leg is the index.** The cache holds Binance's 1d index, premium and mark series for eleven perps.
It holds Binance spot klines only up to 2020-01. With the spot leg at the index and the perp at
`index × (1 + premium)`, the price PnL is exactly the change in basis. Tracking error between Binance spot and
the index is named as unmodelled rather than estimated.

**Why the settlement rule is exclusive at both ends.** The archive's settlement times carry millisecond jitter,
for example 00:00:00.006. A position entered at a day's open would earn or miss that settlement depending on the
jitter. Times are rounded to the hour, and a settlement counts only when the position is open on both sides of
its instant. That choice is conservative for a strategy whose return is the settlement.

**Why liquidation charges no fee.** The exchange's liquidation fee is not a constant this repo records, and an
invented one would move the result. The model takes the whole isolated margin instead, which is the worst case
for the perp leg. It also earns no funding that day.
