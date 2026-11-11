# The rebalancing plan moves BTC alone against cash when BTC is the only group outside its band

- **Why:** Thomas decided Q2's execution exception on 2026-11-11 ("B안으로 정하기") after the rebalancing review.
  Record: `TOTAL_ASSET_ALLOCATION_V0.1.md`, "결정 (Thomas 2026-11-11)".
  - BTC (2.5%, band ±0.625pp) is the group most often outside. A synthetic run on §2's assumptions reproduces §6
    (2.48 trades/yr against 2.5 published). In it, 92–95% of breaches were BTC alone.
  - Under "one outside returns every class", each such breach made the Toss side sell in-band bonds and gold. Their
    gain is taxed at 15.4% as domestic-listed ETFs.
  - With no BTC held, the coin group is always outside, so the plan was "due" every month.
- **Change:** `rebalance.plan` returns `scope="coin_only"` when the coin group is the only one outside and `force`
  is not set.
  - The plan is one manual coin line (SELL from coin, or BUY into it) of the gap to target. Cash takes the
    difference.
  - Under RB4's 50,000 KRW the line is carried.
  - `after` shows the Toss classes unchanged. The terminal render names the case.
- **Unchanged:** targets, bands, the monthly check, the tax order. A breach of any other group, alone or beside coin,
  still returns every class. `--force` still plans every class. The monthly notice still counts the coin group.
- **Downstream:** the G6-sell draft never places a manual line, so a coin-only month drafts no Toss sell.
- **Effect measured on the same paths:**
  - taxed-sell volume per year falls from 3.8% to 1.7% of NAV;
  - realised tax falls from 0.10–0.11%p to 0.06%p, mostly pulled-forward tax;
  - return is +0.02–0.03%p, below the document's 0.06%p noise;
  - drawdown and BTC concentration are unchanged.
- **Checked:**
  - Coin alone above its band and below it.
  - Coin with another group outside.
  - `--force`.
  - A coin gap under RB4's minimum, which is carried.
  - The G6-sell draft with a coin-only plan.
  - The mutations listed in the PR were each caught.
