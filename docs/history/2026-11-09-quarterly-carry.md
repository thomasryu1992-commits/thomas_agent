# 2026-11-09 — Quarterly carry v0.1: a spot-long / dated-futures-short held to delivery, pre-registered

**What.** The second screen candidate under `STRATEGY_SCREEN_GATE_V0.1`, after funding basis.
`docs/proposals/QUARTERLY_CARRY_V0.1.md` pre-registers a cash-and-carry on Binance's USD-M quarterly contracts
(BTC and ETH, the only dated USDT contracts the archive holds). It is screened only if its historical annual
Sharpe is at least 1.0.

Added:
- `scripts/research/quarterly_carry.py`.
- `tests/test_research_quarterly_carry.py`.

**Why equal quantity, not v0.1's equal notional.** A perp trades at the index, so the two sizings coincide there.
A dated contract trades at a basis, and only equal quantity makes `(F0 − S0)/S0` independent of where the price
goes by delivery. A test moves the price 36% and asserts the position earns exactly the locked basis less costs.

**Why exit at the open of delivery day and never read that day's bar.** Trading stops at 08:00 UTC, so the
delivery day's 1d bar is partial, and the spot archive has no 08:00 price to pair with it. Closing both legs at
the close before (00:00 UTC) prices them at the same instant. The cache reader drops the delivery day's bar so
nothing downstream can read it.

**Why a missing quarter blocks the run.** Contract selection takes the nearest delivery. If a contract were
absent from the cache, the next one would quietly become the front and the position would hold a six-month
contract. The reader checks that every quarter's last Friday from the first to the last contract is present.

**Why the liquidation trigger is the last-price high.** The archive has no mark-price klines for dated
contracts. The last price spikes at least as far as the mark, so this side liquidates more often, not less.

**Why Q-POS has no free parameter.** It opens only when the basis it would lock beats the round trip at entry
prices. That threshold is the cost model; a test raises the spot fee and the same basis stops opening.

**Why the price data was fetched after the rules were written.** The load step prints dates and counts only
(a test asserts no price reaches its output), so the rules in §2–§4 were fixed without seeing a basis.

**Why the span ends 2026-09-30, not 2026-10-31.** The load check found no 2026-10-28 row in the USD-M monthly
archive for any BTC or ETH contract, dated or perpetual (other symbols were not checked), and the dated daily
file answers 404; spot has the day. Under the pre-registered gap rule a held position would BLOCK there, so the end moved to the last
complete month without a gap, the same end the funding-basis runs used. No return had been computed.
