# 2026-11-09 — Funding basis v0.2.1: hold only on days the perp traded

**What.** The official `FUNDING_BASIS_V0.2` run printed ENTRY_FAIL. B-POS's number came from one symbol-day.
On 2022-06-01, delisted LUNAUSDT lost 137 times its capital:
- Its last trade was 2022-05-13.
- The cache still had bars after that. The mark froze at 5e-05, and on 06-01 the index jumped to 6.53, another
  asset's price under the same name.
- The index ratio of about 130,000 triggered a reset whose costs were charged on that notional.

Thomas chose to fix the defect and rerun ("결함 고쳐 재실행"). `simulate_member` now:
- holds only on days the perp traded (the archive's 1d kline);
- opens only at a traded close;
- closes at the last traded close before a day it did not trade.

`SPEC` is `FUNDING_BASIS_V0.2.1`.

**Why this is a fix to v0.2, not a new hypothesis.** v0.2's exclusion rule already counted only traded days, on
the premise that nothing could be held on the others (`2026-11-09-funding-basis-v02.md`). The simulator did not
share that premise: it decided holding by whether derivative bars existed. The defect is visible without looking
at returns: positions held on untraded days, and frozen bars. A scan of the cache found two such spans on member
days, LUNAUSDT (48 days) and EOSUSDT (5 days).

**What it costs.** The fixed value was seen before the rerun (post-hoc sensitivity: B-POS about 1.40, B-ALL about
0.51). So the entry floor is no longer a blind test for v0.2.1. The forward screen window is still unseen.

**Test.** `test_bars_that_outlive_trading_are_not_held_even_when_the_index_is_spliced` fails when the traded-day
conditions are reverted.
