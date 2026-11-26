# 2026-11-26 — the mock collector's bars no longer tie at every peak

`runtime/mvp_runtime/crypto/market_data.py` (`MockMarketDataCollector.collect`),
`tests/test_mvp_runtime_crypto_market_data.py`, `tests/test_mvp_runtime_crypto_mining_frame.py`,
`tests/test_mvp_runtime_crypto_point_in_time.py`, `tests/test_mvp_runtime_crypto_factory.py`,
`tests/test_mvp_runtime_crypto_hypothesis_trial.py`.

**What was wrong.** A mock bar opens at the previous close, and its high was the larger of open and close times a
constant 1.005 (its low the smaller times 0.995). So an up bar followed by a down bar gave both bars the same high,
exactly, at every peak, and both bars around every trough the same low. A swing is a strict extreme
(`features._structure_columns`: a tie is no swing), so the mock's own bars never formed one. Every swing-fed column
was empty on them: the break label and size, the order-block columns, the structure leg and the retracement touch.
Ten of the twelve structure families read one of those and were refused on mock bars as naming a feature the
runtime never supplies. `fvg_return_*` was the exception: a gap needs no swing.

Real bars do not do this (how often two neighbouring real bars share a high was not measured).

**What it cost.** Three times in two days a test broke for this reason and not because a rule had changed:

- `2026-11-26-structure-families.md`: the mining-frame test untied the bars by hand, and a second test pinned the
  tie as a fact (`test_on_the_mocks_own_bars_the_break_column_is_empty_rather_than_invented`).
- `2026-11-26-order-block-family.md`: the scheduled mock fire and the cohort trial test were restated to count only
  specs the mock could feed.
- `2026-11-26-fib-return-family.md`: the venue test untied the bars by hand.
- The point-in-time contract test had untied them by hand since `2026-11-25-structure-columns.md`.

Those files stay as written. Where they say the mock ties its highs, that was true until this change.

**Thomas, 2026-11-26: "예, 지어 줘"** — build the PR that unties the mock. What he was shown as its scope: the two
lines in the mock collector and the one test that pinned the tie, with the hand-unties and the two restated tests
to be settled in the PR.

**The change.** The high is the larger of open and close times `1.005 + 0.002 × h`, the low the smaller times
`0.995 − 0.002 × l`, where `h` and `l` come from the bar's own hash (`_frac` over symbol, timeframe and the bar's
number; sha256, so the same on every Python version and operating system). The high's factor is in [1.005, 1.007)
and the low's in (0.993, 0.995].

What did not change. Held by tests: a bar contains its open and close; bar `i` depends on symbol, timeframe and
`i` alone, never on how many bars were asked for (the point-in-time contract re-collects a shorter frame and
compares bar for bar); the `is_synthetic` mark. Compared directly against the collector before this change, over
6,000 bars (three symbols, four timeframes): the only candle fields that differ are `high` and `low`, and no
snapshot field differs. So the close walk, volume and taker legs are the numbers they were; no test pins that.

**What Claude chose in the PR** (written in the loop record §201 before the code):

- The wick formula above.
- **The three hand-unties are removed.** Those fixtures now run on the mock's own bars, which is the stronger
  test: the point-in-time contract checks the structure columns for look-ahead on bars nobody adjusted.
- **The two restated tests keep their allowance, pointed at what is still true.** A spec may still be lost on mock
  bars when its family reads a column from another feed (open interest, liquidations, positioning:
  `_UNREACHABLE_WITH_A_MOCK`), and only then. They were not put back to "exactly eight stored": that it passes
  today is where the rotation happens to stand, which is the coupling those tests were restated to drop.
- The test that pinned the tie now pins the opposite, at 1h, 4h and 1d: on the mock's own bars a level is broken,
  an order block and a gap are touched and a leg is retraced.

**How it was checked.** Before the build, in a throwaway worktree that was never committed: with the two lines
changed and nothing else, the full suite on this host had exactly one failure, the test that pinned the tie. Seven
deliberate breakages of the new lines each fail a test (either wick constant again, a wick keyed on the bar count,
a high below the body, a low above it, a band twice as wide, one hash shared by every bar).

The two tests' allowance ("a spec is lost only when its family reads a column the mock cannot feed") was walked
over the whole library, not only the slice today's rotation draws: on mock snapshots with the mining legs attached,
every template at its base draw, for BTCUSDT and ETHUSDT at 1d and 1h and ETHUSDT at 4h. At 1d, the context both
tests fire in, no family is refused at all, so there the allowance has nothing to allow today. At 1h and 4h four
families are refused (`oi_squeeze_*`, `oi_unwind_*`), each for an open-interest column in `_UNREACHABLE_WITH_A_MOCK`.
No family is refused for a column outside that set.

**What changes outside the tests.**

- On a machine whose market-data opt-in is absent the collector selection returns the mock, so a factory fire there
  mines on mock bars. Candidates mined that way carry no synthetic mark: a search of the factory, the pool, the
  promotion door and the forward cohort finds no line that reads one. Whether another gate (cost basis, evidence
  depth) would refuse a mock-mined candidate anyway was not read. Trading on synthetic data is refused by the data-health guard,
  and the candle archive refuses synthetic bars. That was already so for every family the mock could feed; this
  change extends it to the ten swing-fed ones. **No guard is added here.** It is recorded as a follow-up, not fixed.
- The Docker smoke job ticks the scheduler once on an empty state directory and expects "fired 0"; it runs no
  crypto fire and reads no mock candle.
- The mock's highs and lows are different numbers from before. Its closes are not.

**Not known.** Whether any stored record on some machine was produced from mock bars and is compared against a
fresh mock run. None was found in the tests.
