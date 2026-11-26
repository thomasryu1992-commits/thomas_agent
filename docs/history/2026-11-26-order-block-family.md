# 2026-11-26 — the order-block pair, and one more column admitted to the vocabulary

`runtime/mvp_runtime/crypto/template_space.py` (`order_block_return_long` / `_short`, `STRUCTURE_FAMILIES`),
`runtime/mvp_runtime/crypto/features.py` (`STRUCTURE_MINTABLE_NUMERIC_COLUMNS`), `scripts/selection_evidence.py`
(`ECONOMIC_FAMILY_MARKERS`), `tests/test_mvp_runtime_crypto_structure_families.py`.

**What it is for.** The second of the two order-block PRs. The first (`2026-11-26-order-block-columns.md`) put four
order-block columns on the row as evidence only. This one lets the factory read one of them and adds the pair that
does. The working record is `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §191 – §193 on the research branch (PR
#1474, unmerged), so this file carries what the code rests on.

**No measured edge stands behind this pair.** The hypothesis form has lines 1 – 3 empty (§191.7): no measured
counterparty, no reason it would persist, and the only crypto figure found is negative and from one vendor. By the
form's own rule it would not be raised. Thomas chose to raise it; the prior written beside it is about 2%.

**What Thomas decided** (2026-11-26, "예, 선례대로"; the table is the one the question showed):

| Decided | Value, the `fvg_return_*` precedent |
|---|---|
| Direction | a bar that comes back to an up block is a long, to a down block a short. One pair |
| Threshold | the touched block's size, 0.05 to 1.0 ATR, base 0.3 |
| Exit | the trend exit space |
| Timeframes | 1h, 4h and 1d |
| Judgement cell | one new cell, appended to the marker table |

**What Claude chose, written in §193.2 before any code:** the names `order_block_return_long` / `_short`; the
parameter `block_atr_min` on a space of its own (same numbers as the gap's, another column); one admitted column,
`order_block_touch_size_atr`, with one condition a side (an up block's size is positive, a down block's negative,
no touch is 0.0, so the label adds nothing); the existing `STRUCTURE_FAMILIES` gate instead of a new one; the
marker `("order_block", "zone(order_block)")`; the feed is candles.

**What was built.**

- Two templates, appended after the eight structure templates, so every earlier family keeps its index.
- `order_block_touch_size_atr` is in the numeric vocabulary and classified as a candles feature. The other three
  order-block columns stay evidence only; the validator still refuses them and the proposal prompt still does not
  list them. Structure columns: four mintable, nine evidence.
- The pair is in `STRUCTURE_FAMILIES`, so it mints at 1h, 4h and 1d and not at 15m.
- One marker appended to `ECONOMIC_FAMILY_MARKERS`; the pinned hash moved with this decision
  (`16cadabf…bc4202` → `aba353d8…f80de5`). A test calls the classifier with and without the marker over every
  family the library has held: only the two new names change cell.
- No row key was added: the memory cost of the columns PR is unchanged by this one.

**Two things known before the build, on synthetic bars only** (20 seeds × 3,000 bars, bars that open away from the
previous close, the merged columns; no market data was read):

1. **The threshold barely bites in its lower part.** A block is one whole bar's range and the ATR is close to the
   average bar range, so a touched block's size sits near one ATR (median 0.68 to 0.84 across three wick sizes),
   where a touched gap's median is 0.28 to 0.51. Of touched blocks, 99.9 – 100% pass the floor of 0.05, 87 – 95%
   pass the base of 0.3 and 27 – 34% pass the ceiling of 1.0. So between 0.05 and 0.3 the parameter filters about
   one touch in ten, and it is still counted as a free parameter. The range is Thomas's by the table and was not
   changed; this was put at the top of the merge question.
2. **A 1d holdout is under the 25-trade floor at every draw.** Long-side matches per 600 bars (the holdout of a
   2,000-bar 1d frame): 21 – 25 at the floor, 20 – 22 at the base, about 7 at the ceiling. Matches are an upper
   bound on trades. At 4h (1,800 holdout bars) and 1h (7,200) the same counts are three and twelve times larger.

**How it meets the families already there.**

- **`fvg_return_*`, same side.** One bar can come back to a block and a gap at once, and both pairs then enter the
  same way. That is not the follow/fade meeting. `paper.route_entries` takes one primary among same-direction
  matches in a context (realized expectancy, then champion score) and settles the others behind it
  (`ROUTED_BEHIND_PRIMARY`): one entry, no stacked exposure. A test pins it with one strategy of each pair. On
  synthetic bars the share of this pair's long matches that `fvg_return_long` also takes at the same draw is 30 –
  43% at the floor, 13 – 27% at the base and 1 – 7% at the ceiling; it moves with the arbitrary wick size.
- **`structure_follow_*`, opposite side on a break bar.** A bar that breaks down while touching an up block is a
  long for this pair and a short for the follow families. That falls under the existing direction-conflict rule
  (`2026-11-26-structure-families.md`).
- **Pool.** The family cap is two occupied slots per family name (`OBSERVATION_FAMILY_CAP`), so the new names have
  caps of their own. No rule controls correlation between families.

**What changes on deploy.** The library goes from 50 to 52 at 1h and 4h (48 to 50 for the reference symbol) and
from 34 to 36 at 1d (32 to 34); 15m is unchanged. Each context's rotation position shifts once, as it did with the
structure pairs. `judgement_fingerprint()` is `f50dfce1c444`, unchanged.

**Tests that were tied to the library's size, and what was done with each.** Four tests in
`test_mvp_runtime_crypto_factory.py` failed, none because a rule had changed:

- The strided-walk test's starvation half had nothing to show on ETHUSDT 1h at 52 families, where
  `gcd(15 × 4, 52)` is the slice width. It now asserts the cursor on four libraries and requires the starvation to
  show on at least one. With the cursor disabled it fails.
- The scheduled mock fire stored four candidates instead of eight because its slice drew structure families the
  mock's bars cannot feed (the mock ties its highs at every peak, a tie is no swing). It now asserts that the only
  specs lost are ones whose family reads a swing-fed column.
- The shortfall-slice test compared stored candidates, and the control's slice landed on the cross-section pair,
  which the fixture has no peer candles for. It now compares the families each fire drew. With the shortfall made
  to redraw the batch's own slice it fails.
- The multiple-of-eight test named BTCUSDT 1h, now 50 families. It takes whichever mined context has a multiple
  of eight today (BTCUSDT 15m, 40).

**Not known.** Everything about market bars: how large touched blocks are there, how often this pair and
`fvg_return_*` share a bar, how often real bars tie at a peak, and whether any timeframe's holdout is judgeable.
Which timeframes this machine's schedules mine was not read.
