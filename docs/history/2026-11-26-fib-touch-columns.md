# 2026-11-26 — retracement columns on the feature row, as evidence only

`runtime/mvp_runtime/crypto/features.py` (`_structure_columns`, `FIB_COLUMNS`, `FIB_RETRACE_LEVELS`),
`tests/test_mvp_runtime_crypto_fib_touch_columns.py`, and the structure test files that count the columns.

**What it is for.** The last indicator on the list Thomas gave for the second layer (2026-11-25): Fibonacci
retracements. Its definition sheet is `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §194 (on the research branch,
PR #1474, not merged, so this file carries what the code rests on). The sheet asked one question: carry the columns
as evidence only, and with which leg and which form. Thomas, 2026-11-26: **"예, 구조 구간에 꼴 가로"** — yes, on the
structure leg, as fixed-ratio touches.

**Why the leg is not the last two confirmed swings.** That is the straightest definition, and the sheet tried it
first on synthetic bars. A swing is confirmed two bars late, and those two bars are bars with lower highs: the
wait for the confirmation is the start of the retracement. That part is true on any bars, by the definition. On
the synthetic bars the legs were about three bars long, so by the bar a leg became known its retracement had a
median depth of 0.72 to 0.80, and 80 – 90% of legs were already past 0.382. How long legs are on market bars, and
so how much is over before the leg is known, was not looked at. The leg below does not wait for its end.

**The definition** (closed bars only; the up side is written, the down side mirrors):

| | Definition | Known when |
|---|---|---|
| Turn | a break against a known state (a CHoCH). The bar a frame first learns a state on is not a turn | that bar's close |
| Origin | when the state turns up at bar f, the lowest low from the previous turn to f. The later bar on a tie | bar f's close |
| End | the highest high from the origin's bar on, taken bar by bar. Nothing is waited for | every bar |
| Depth | (the end as of the bar before − this bar's low) ÷ (that end − the origin) | that bar's close |
| Touch | the bar's depth reaches 0.382, 0.5 or 0.618 for the first time since the end last moved. The bar carries the deepest level it newly reached | that bar's close |
| Life | the leg ends when the state turns again. There is no bar count | |

Edge rules:

1. The turn bar carries no level: it makes the leg's end.
2. A bar that retraces and also makes a new end is read retracement first, then the end moves and the record
   starts again. The order inside a bar is not known, so this is a rule.
3. A depth beyond the origin counts as the deepest level. A bar that CLOSES through the structure the other way is
   a turn (rule 1), so it carries nothing.
4. A bar wholly beyond the leg's end is no retracement.
5. There is no tolerance: a low at or past the level is a touch. The comparison is made on the ratio, which is a
   quotient of floats; the tests keep their hand-built lows clearly either side of a level.
6. The touch is a ratio and is not divided by ATR. Only the leg's size is.

**What Thomas chose, what followed, and what Claude chose.**

- Thomas chose: carry the columns as evidence only; the structure leg; the fixed-ratio form.
- Followed because no other value was named when the sheet listed them: the three ratios (0.382, 0.5, 0.618, the
  indicator's own; 0.786 is not among them) and no tolerance.
- Claude chose, written in §194 before any code: the origin's span (turn to turn; an origin taken from the last
  opposite break is another way to write it), the retracement-first rule, the two names, that the leg's size alone
  is empty while the ATR is not ready, and that a hole in the candles forgets the turns.

All of them were fixed before any result and do not move afterwards.

**What a row now carries** (two more columns, each also as `prev_*`; fifteen structure columns in all):

| Column | Meaning |
|---|---|
| `structure_leg_size_atr` | the live leg's size (end − origin) ÷ this bar's ATR, signed by the leg's direction. A state: it has a value on every bar a leg exists |
| `fib_touch_ratio` | 0.382, 0.5 or 0.618 on the bar that first reaches that level, signed by the leg's direction; 0 otherwise |

**Evidence only.** Both are on the row and in neither mintable vocabulary. No template reads them, the validator
refuses a spec naming one, and the proposal prompt does not list them. The mintable four, the five family pairs,
the three threshold spaces, the timeframe gate and the judgement markers are untouched. Admitting a column or
adding a family is a later decision of Thomas's.

**Nothing here can show the ratios are special.** The outside survey (§186, §194.1) found abstract-level evidence
that price behaves no differently at Fibonacci zones than at other ratios, and no source comparing after-cost
results of Fibonacci entries with entries at arbitrary ratios. These columns carry three levels and no control at
other ratios. What a family built on them would test is a pullback's depth against the last structure leg, on a
grid of three depths.

**Not built.** The other form (the depth as a value whose ratios become a family's thresholds) and the leg from
confirmed swings. The first needs two event columns: a band's upper bound on one column is true on a quiet bar, so
a spec the ablation lattice reduced to that one condition would match nearly every bar.

**The start-cut rule.** A column must say the same thing or nothing when the frame starts later. The origin is
searched from the previous turn, so both columns are empty until the frame has seen the state turn twice. The bar
a frame first learns a state on is not a turn: counted as one, it gave 5,278 different values in 1.1 million on
the sheet's synthetic windows. On those windows the last bar's value was empty in 1.6% of 120-bar frames (the
live cycle's default) and in none of the 200-bar ones.

**How it was checked** (synthetic and hand-built bars only; no market data was read):

- The thirteen earlier columns against `origin/main`'s function, on walks with holes and unreadable opens: 780,000
  values, 0 different.
- The two new columns against the scratch pass the sheet's numbers came from, at its three wick sizes: 180,000
  values, 0 different, and the same 250 / 261 / 292 touches per 3,000 bars.
- Fifteen deliberate breakages of the new code each fail a test: first sight counted as a turn, one turn being
  enough, the turn bar carrying a level, the end read before the retracement, the record not restarting, the
  shallowest of several levels, beyond the origin not counting, an unsigned touch, the earlier bar on a tie, a
  hole not forgetting the turns, a changed ratio, a tolerance, the origin searched from the frame's start, the
  leg's size keeping its first end, and a column admitted to the vocabulary.
- `judgement_fingerprint()` is `f50dfce1c444`, as before. The library is unchanged (54 templates), so no rotation
  position moves.

**What it costs.** A row goes from 178 to 182 keys and stays at 6,576 bytes: the table that doubled at the 171st
key does not double again until the 342nd. One frame of factory depth at 1h (24,000 synthetic bars, candles only):
peak memory 349 MB → 354 MB, time unchanged within noise (3.4 – 3.5 s), on the host's Python 3.14.

**Not known.** Everything about market bars: how often the state turns, how large legs are, how often a level is
touched, and how much those bars coincide with the bars `fvg_return_*`, `order_block_return_*` and
`trend_pullback` already enter on (on synthetic bars 45 – 59% of touches shared a bar and side with one of them at
its base draw, moving with the arbitrary wick size, so it says nothing either way). The hypothesis form for a
family has lines 1 – 3 empty and a prior of about 1% (§194.8).
