# 2026-11-19 — discovery loop round 68: the input-only doors for the two hourly variables

**Shipped:** `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §86 (registration), a row in
`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1.md`, the status line, and code: `scripts/research/hourly_vars.py`,
`signal_lab` overlap candidates `price_delay_30` and `volume_peak_share_30`, the unregistered kin `r2_btc_30`, and the
`hourly-doors` command, with `tests/test_research_hourly_vars.py`.

**Why.** §85 fixed the two variables and named three input-only doors before either reaches the registry.

**Choices that are not obvious.**

- *Pure Python.* The research venv has no numpy and adding a package is not this round's to decide. BTC's
  cross-products are prefix sums built once; a coin's regression is one pass over its own window (about 0.7 ms).
  Only coins in the month's top 100 are computed, at the weekly decisions.
- *The window ends when the decision day's daily bar closes.* Daily variables read the decision day's bar (it closes
  at the next day's 00:00 UTC); §85 wrote "before d 00:00", which contradicted the principle it stated (never read
  past the daily variables' last instant). The principle governs and a test pins it.
- *A coin hour missing.* The BTC prefix sums count every hour whose seven BTC returns exist; an hour the coin lacks is
  taken back out, so the regression is exactly the one over the rows both have (tested against a direct fit).
- *Overlap judged as §40 judged it,* on the top tier's median, with N1's own same-window R² among the kin.
- *A correction before any output.* Coverage's second ratio counted the span's last month as "leaving the top 100",
  because the month after it is outside the span; those cells now count on neither side. The first chain was stopped
  while it waited for CI, before the run started.
