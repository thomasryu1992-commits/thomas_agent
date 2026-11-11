# 2026-11-11 — the metrics basket screen (M04 forward, 2027-03..06)

Thomas answered Q6 of `METRICS_BASKET_SEARCH_V0.1` with "등록 진행": M04 gets the four-month forward screen the
protocol's §3 named. `scripts/research/metrics_basket_screen.py`, `docs/proposals/METRICS_BASKET_SCREEN_V0.1.md`.

**Why it is not a copy of the momentum screen.** Three things that screen does not need, or gets wrong:

- *Funding after a delisting.* A delisted coin keeps flat zero-volume klines and its funding files stop (HIFIUSDT,
  2025-10). The momentum screen asks for a settlement on every day a member has a bar, so a member delisted inside
  the window would hold it at NOT_YET for good. Here a settlement is required only on a day the coin traded, and a
  day without one is excused when that month's funding file is on disk. The momentum screen is left as registered;
  the gap is a follow-up for its owner's decision (taken up the same day: `2026-11-11-momentum-screen-funding-coverage.md`).
- *Metrics the archive had not published yet.* `binance_archive.fetch` never asks again for a name in
  `missing.json`. A day asked before the archive publishes it (day D-1 was a 404 on 2026-11-11, D-2 was served)
  would stay "not served" and read as an unrankable coin, which is how M03 came to rank 22 of 30. So `refresh`
  never asks for a day younger than three days, drops the plan's days from `missing.json` before each fetch, and
  leaves a marker; the screen is NOT_YET until that marker reaches the window's last day.
- *Operations that outlive a session.* The fetch that refilled the cache for M04 was a scratch driver. The metrics
  fetch, the members' funding and `build` are now step 6 of `funding_basis_refresh`, through
  `binance_archive`'s own readers: no new network call site.

**What was not built.** No variant of M04 (a funding cap, an excluded coin): the locked span is spent, so a variant
could only be a new forward hypothesis. No Sharpe is printed; the verdict is the sign of the mean.

**Low power, stated up front.** The locked span's sum came from a few coins on hourly funding. A four-month window
without such an episode judges something else than what passed. The split by settlements a day is shown beside the
verdict for that reason.
