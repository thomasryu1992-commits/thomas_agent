# 2026-11-26 — the retracement pair, and one more column admitted to the vocabulary

`runtime/mvp_runtime/crypto/template_space.py` (`fib_return_long` / `_short`, `_fib_level`, `STRUCTURE_FAMILIES`),
`runtime/mvp_runtime/crypto/features.py` (`STRUCTURE_MINTABLE_NUMERIC_COLUMNS`), `scripts/selection_evidence.py`
(`ECONOMIC_FAMILY_MARKERS`), `tests/test_mvp_runtime_crypto_structure_families.py`.

**What it is for.** The second of the two retracement PRs. The first (`2026-11-26-fib-touch-columns.md`) put two
retracement columns on the row as evidence only. This one lets the factory read one of them and adds the pair that
does. It is the last indicator on the list Thomas gave for the second layer. The working record is
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §194 – §195 on the research branch (PR #1474, unmerged), so this
file carries what the code rests on.

**No measured edge stands behind this pair, and there is evidence against what its name claims.** The hypothesis
form has lines 1 – 3 empty (§194.8). The outside survey found abstract-level evidence that price behaves no
differently at Fibonacci zones than at other ratios. Asked whether to add the pair or close the layer, Claude's
default was to close it, for three reasons: that evidence, that this is a third "come back to where the move
passed" pair beside `fvg_return_*` and `order_block_return_*`, and that leaving it out wasted almost nothing.
Thomas chose to add it (2026-11-26, "계열 한 쌍 더함"). The prior written beside it is about 1%.

**It cannot show the ratios are special.** There is no control at other ratios. What the pair tests is a pullback's
depth against the last structure leg, on a grid of three depths. A pass would say that, and nothing about 0.382,
0.5 and 0.618 in particular.

**What Thomas decided** (2026-11-26, "예, 그렇게"; the table is the one the question showed):

| Decided | Value | Where it comes from |
|---|---|---|
| Threshold | one of three levels (from 0.382, from 0.5, 0.618 alone), drawn evenly | the `session_index` shape. New with this pair |
| Direction | a bar that comes back to a level of an up leg is a long, of a down leg a short | the `fvg_return_*` / `order_block_return_*` precedent |
| Exit | the trend exit space | the same precedent |
| Timeframes | 1h, 4h and 1d | the same precedent |
| Judgement cell | one new cell, appended to the marker table | the same precedent |
| Column admitted | `fib_touch_ratio` alone | one condition a side. The leg's size stays evidence |

**Why the threshold is an index and not a range.** `fib_touch_ratio` takes three values, so "touch ≥ r" has three
meanings and no more. With a continuous range most of the range changes nothing (every value under 0.382 behaves
the same, and so does every value between two levels), it is still counted as a free parameter, the draw moves in
a small strip around its base and seldom crosses a level, and a value drawn above 0.618 never fires. With an index
the entry side has exactly three conditions a side and each comes up a third of the time.

One reason first given for this was wrong and was corrected in the record (§195) before the build: that the index
leaves "three rule hashes a side". The rule hash covers the exits too, and those differ on nearly every draw, so
hashes are distinct under either form and duplicates were never the issue.

**What Claude chose, written in §195 before any code:** the names `fib_return_long` / `_short`; the parameter
`fib_level_index` (0, 1, 2 for the three levels), integer and declared unordered, which is what makes the draw
even (the levels do have an order, but an ordered integer over [0, 2] is perturbed around its base and rounded,
and stays there); base index 0, which the draw does not read; the marker `("fib_", "retrace(fib)")`; the existing
`STRUCTURE_FAMILIES` gate; the feed is candles.

**What was built.**

- Two templates, appended after the ten structure templates, so every earlier family keeps its index.
- The literal on the condition is the level itself, taken from `features.FIB_RETRACE_LEVELS`, the tuple the column
  is built from. The boundary is float equality ("touch ≥ 0.5" against a value of exactly 0.5), and a retyped
  number is another float. A test builds rows from candles and holds each index to exactly its bars.
- `fib_touch_ratio` is in the numeric vocabulary and classified as a candles feature. `structure_leg_size_atr`
  stays evidence only: it is a state, so as a filter it would be a condition that is true on quiet bars. Structure
  columns: five mintable, ten evidence.
- The pair is in `STRUCTURE_FAMILIES`: 1h, 4h and 1d, not 15m.
- One marker appended; the pinned hash moved with this decision (`aba353d8…f80de5` → `1eda512d…a99ce5`). The marker
  tests now call the classifier with and without each appended marker: only that marker's families change cell.
- `fib_level_index` is the second unordered parameter in the library. The test that pinned `session_index` as the
  only one now names both, and says a third is a decision to record.
- No row key was added.

**Known before the build, on synthetic bars only** (20 seeds × 3,000 bars, bars that open away from the previous
close, the merged columns; no market data was read). Long-side matches for one symbol:

| Wick size | from 0.382 | from 0.5 | 0.618 alone |
|---|---|---|---|
| 0.15% | 126 per 3,000 bars (25.1 per 600) | 72 (14.3) | 36 (7.2) |
| 0.3% | 134 (26.7) | 73 (14.6) | 35 (7.1) |
| 0.6% | 150 (30.0) | 83 (16.5) | 39 (7.8) |

600 bars is one symbol's holdout in a 1d frame, and the holdout floor is 25 closed trades; matches are an upper
bound on trades. So for ONE symbol at 1d, two of the three settings sit under the floor. A 4h or 1d fire whose
schedule names a cohort pools its legs' holdout tails, so the floor is met by the sum; whether this machine's
schedules name a cohort was not read.

**How it meets the families already there.** Same side as `fvg_return_*` and `order_block_return_*` when one bar
comes back to more than one of the three; `paper.route_entries` takes one primary among same-direction matches
and settles the rest behind it, so nothing stacks. On synthetic bars 45 – 59% of touches shared a bar and side
with one of `fvg_return_*`, `order_block_return_*` or `trend_pullback` at its base draw, moving with the arbitrary
wick size, so the figure says nothing either way. A touch never falls on a bar that breaks the structure the other
way (that bar is a turn and carries none), so it does not meet `structure_fade_*`.

**What changes on deploy.** The library goes from 54 to 56: 1h and 4h 52 → 54 (50 → 52 for the reference symbol),
1d 36 → 38 (34 → 36); 15m is unchanged. Each context's rotation position shifts once.
`judgement_fingerprint()` is `f50dfce1c444`, unchanged.

**One test moved with the library's size.** `test_the_factory_mines_the_venue_the_snapshot_came_from` mints from
whichever slice the rotation lands on, by design, and at 52 families (BTCUSDT 1h) that slice became the four
`structure_follow_*` templates. The mock collector's bars tie at every peak, a tie is no swing, so those specs were
refused as unsuppliable and nothing minted. No rule had changed. The fixture's highs and lows are now moved apart
by a hair, as the mining-frame test already does, and the test's own docstring records why. The six tests restated
for the order-block pair did not move again.

**Not known.** Everything about market bars: how often the state turns, how often a level is touched, how often
this pair shares a bar with the other two return pairs, how often real bars tie at a peak. Which timeframes this
machine's schedules mine and whether they name cohorts were not read. Whether any stored label already contains
`fib_` was not read; one that did would now be read into the new cell.
