# 2026-11-13 — the strategy search rebuilt from the layer underneath: a map of where a variable ranks returns

`scripts/research/signal_lab.py`, `docs/proposals/STRATEGY_RESEARCH_REBUILD_V0.1.md`.

**What it is for.** Thomas (2026-11-13): months without one meaningful result; keep the outer skeleton, and rebuild
how strategies are found — split the market into scenarios, look for the edge and the variables in each, improve the
tools if they keep failing. The tools so far measured one number, a finished basket's net Sharpe. That number folds
the signal, the portfolio and the costs together: a weak signal and no signal both read "missed", and nothing says in
which market state, which coins or which holding period a variable works.

**What was built.** A map of cells — variable × liquidity tier × horizon × scenario — each holding the rank
correlation between the variable at a weekly decision and the return that follows. Returns run from the next open,
between traded days only, before costs. Scenarios are read at the decision from the past only: trend (the existing
regime label), Bitcoin's volatility, the dispersion of returns, the level of funding, and trend crossed with
volatility. Rank 31–100 is read for the first time.

**How chance is handled.** These years had been read 22 times and the grid has over a thousand cells, so the best
cell looks good with no signal. Forty random variables go through the same grid — half redrawn weekly, half fixed per
coin like a slow characteristic — and a real cell is read against what they reach. They reached |t| 4.3: "t above 2"
means nothing here. The rule for a lead (above the random maximum, a rank that persists week to week, the sign of its
own all-weeks cell, at most three) was fixed and pushed before the run.

**Nothing after 2024 is read.** The market is cut at the span's last day and a decision whose return window would
cross it is dropped; a test shows that later bars, whatever they hold, change no cell. The confirm span
(2025-01..2026-09), which Thomas reopened for one read, is kept for the single final candidate.

**Recorded rules this steps past**, on Thomas's words of the day, each listed in the proposal so he can object to
one: no pooling of missed results, the confirm span being spent, no reading of a reversed sign, and the count of
reads of these years.

**What the first map said.** One thing stands above the random variables everywhere: low-volatility coins lead the
following week, in every scenario and both tiers. In rank 31–100 the rank and the mean disagree — high-volatility
coins lag most weeks and win big occasionally — which is a candidate reason the earlier low-volatility basket was
weak. Funding and momentum change sign with the scenario (each cell inside chance), so the earlier "funding carry is
null" was an average of opposite states. The plain pool of the sixteen registered definitions reaches an annual
Sharpe of 0.897 at 6.7% a year: above every member, short of the entry rule, flat in 2023–2024.

**Checked.** Two cells recounted by a separate script that shares none of the lab's grid, return or correlation
code: identical. The baseline's eleven members with the same span reproduce their owner documents' Sharpe to three
decimals.

**Not done.** No strategy, no costs, no confirm read. The layers after this one are each registered separately.
