# 2026-11-09 — Funding basis v0.2: the carry over a point-in-time monthly top-30 universe

**What.** `FUNDING_BASIS_V0.1` ran on eleven fixed majors and failed the one-month gate. Under the four-month
screen that replaced that gate (`STRATEGY_SCREEN_GATE_V0.1`), a hypothesis is screened only if its historical
annual Sharpe is at least 1.0. `docs/proposals/FUNDING_BASIS_V0.2.md` pre-registers the same carry over each
month's top 30 perps by the previous month's volume, kept only when Binance spot listed a pair then.

Added:
- `scripts/research/funding_basis_v02.py`.
- `tests/test_research_funding_basis_v02.py`.

**Why a point-in-time universe, delisted perps included.** Fixing today's top 30 would keep only survivors. For
this carry, survivorship matters in both directions:
- Delisted coins often crashed. The hedge absorbs that.
- Delisted coins also tend to be the ones whose funding went deeply negative and whose perps liquidated shorts.

The public futures endpoints still serve a delisted perp's index, premium and mark history (checked on MATICUSDT
before pre-registration), so the universe can be built as it was known each month.

**Why the simulation is a copy with two additions, not a change to v0.1.** v0.1 is a merged, judged
pre-registration. `simulate_member` adds membership (returns only on member days) and an exit at the close
before a symbol leaves or its bars end. For a symbol that is a member every month, a test asserts it returns
exactly what `funding_basis.simulate_symbol` returns.

**Why missing data excludes a month instead of blocking the run.** v0.1 blocked on any missing bar while
holding. That is affordable on eleven majors and almost certain to fire across 311 symbols. The rule asks
whether the perp traded on a day (the archive's 1d kline) that lacks a derivative bar, or whether a month
settled no funding. Either one excludes that symbol-month. Every exclusion is listed. More than 5% of
member-months excluded blocks the run, so exclusions cannot quietly reshape the universe. Days a delisted perp
did not trade are not exclusions: nothing could be held then.

**Why `1000`-prefixed perps map to the unprefixed spot pair.** `1000PEPEUSDT` quotes a thousand PEPE. The model
works in price ratios to the entry (`S/S0`, `P/P0`), so the unit cancels.
