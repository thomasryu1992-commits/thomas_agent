# 2026-11-26 — order-block columns on the feature row, as evidence only

`runtime/mvp_runtime/crypto/features.py` (`_structure_columns`, `ORDER_BLOCK_COLUMNS`),
`tests/test_mvp_runtime_crypto_order_block_columns.py`, and the three structure test files that loop over
`STRUCTURE_COLUMNS`.

**What it is for.** The third step of the price-structure line (`2026-11-25-structure-columns.md`,
`2026-11-26-structure-families.md`). Thomas named the order block among the indicators the factory has no column
for. Its definition sheet is `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §191 (on the research branch, PR
#1474, not merged, so this file carries what the code rests on). The sheet put one question: carry the columns as
evidence only, and which bar is the order block. Thomas, 2026-11-26: **"예, 가까운 봉으로"** — yes, and the block is
the opposite bar nearest the break.

**The definition** (closed bars only; O, H, L, C are a bar's open, high, low, close; the up side is written, the
down side mirrors):

| | Definition | Known when |
|---|---|---|
| Opposite bar | for an up break, a bar that closed below its own open: C < O. C = O is not one | that bar's close |
| The broken swing's bar s | the bar of the swing high whose level the break bar t closed above | bar t's close |
| Order block | the last opposite bar in s .. t − 1, searched from t − 1 backwards. The swing bar itself is inside the search; nothing before it is | bar t's close |
| Zone | that bar's low to its high | bar t's close; before that it does not exist |
| Touch | a bar AFTER t whose low reaches the zone's top (the near edge). The zone is then gone | that bar's close |
| Life | an untouched zone is dropped after 48 bars | |

Four edge rules:

1. The break bar never touches the zone it made. It has usually just travelled through it.
2. No opposite bar in the span, no zone. No other bar stands in.
3. A zone whose top is not below the break bar's close is no zone: price never left it.
4. A bar that reaches several zones reports the most recently born one, and all of them are gone.

Swings, levels and breaks are the merged ones, computed in the same pass, so there is one authority for what a
break is. The sheet also described a second form (the opposite bar at the start of the move). Thomas chose the
nearest bar; the other form is not built.

**The zone's range, life and touch depth are the gap's.** The sheet listed the three with the FVG precedent beside
each (whole range, `FVG_BAND_LIFE_BARS` = 48, near edge) and said a different value had to be named before the
build. None was, so the precedent applies. They are not values Thomas picked. No second life constant exists: the
code reads `FVG_BAND_LIFE_BARS`. As with the three structure constants, they were fixed before any result and do
not move afterwards.

**Three things the sheet did not settle, chosen by Claude before any code** (recorded in §191 first):

- **Names.** `order_block_size_atr`, `order_block_touch` (`NONE`, `UP_BLOCK`, `DOWN_BLOCK`),
  `order_block_touch_size_atr`, `order_block_touch_age_bars`. The same shape as the four gap columns.
- **An open that cannot be read.** The search walks back from t − 1. If it meets a bar whose open is not a number
  before it meets an opposite bar, there is no zone: that bar may have been the last opposite bar. The nine
  earlier columns do not read the open and are unchanged by it.
- **A zone born before the ATR is ready.** The zone exists and can be touched; only its size is None. A gap's
  band does the same.

**What a row now carries** (four more columns, each also as `prev_*`; thirteen structure columns in all):

| Column | Meaning |
|---|---|
| `order_block_size_atr` | width of a zone born on this bar ÷ this bar's ATR, signed by the break's direction; 0 when no zone was born |
| `order_block_touch` | `UP_BLOCK` / `DOWN_BLOCK` when this bar reached a zone, else `NONE` |
| `order_block_touch_size_atr` | the reached zone's size as it was born; 0 with no touch |
| `order_block_touch_age_bars` | bars since that zone was born; 0 with no touch |

**Evidence only.** The four are on the row and in neither mintable vocabulary. No template reads them, the
validator refuses a generated spec, fusion child or proposed trial that names one, and the proposal prompt does
not list them. `STRUCTURE_EVIDENCE_COLUMNS` goes from six to ten; the mintable three, the four family pairs, the
two threshold spaces, the timeframe gate and the judgement markers are untouched. Admitting a column or adding a
family is a later decision of Thomas's, asked on its own.

**The start-cut rule is not the gap's.** A column must say the same thing or nothing when the frame starts later
(the live cycle reads 120 bars, the replay reads the factory depth). A gap needs three bars, so its touch waits
one band life from the frame's first bar. A zone is born at a break, and a frame knows every break only once it
has confirmed a swing on BOTH sides. So the touch is None until one band life has passed since that bar. With the
gap's rule a frame longer than the band life reports `NONE` on a bar where the full history reports a touch of a
zone whose swing sits before the frame; `test_a_frame_that_cannot_know_a_zone_says_nothing_rather_than_none` is
that case built by hand. `order_block_size_atr` does not wait: a break's own span is always inside the frame.

**How it was checked** (synthetic and hand-built bars only; no market data was read):

- The nine earlier columns against `origin/main`'s function, on plain walks, walks whose bars open away from the
  previous close, walks with holes in high/low/close and walks with unreadable opens: 1,080,000 values, 0
  different.
- The four new columns against the scratch pass the sheet's numbers came from (§191.4), on its 20 seeds: 240,000
  values, 0 different, and the same 245 touches per 3,000 bars.
- The sheet's own runs had every bar open at the previous close, where "closed below its open" and "closed lower"
  are the same bar. The tests here use bars that open away from the previous close (the two differ on about one
  bar in eight).
- Thirteen deliberate breakages of the new code each fail a test in the new file: the gap's start-cut wait, a
  break bar touching its own zone, an equal close counted as opposite, the both-sides marker surviving a hole, a
  column admitted to the vocabulary, a search that runs past the swing bar, the edge rule dropped, an unreadable
  open skipped, a life one bar short, the earliest of several zones reported, an unsigned size, reached zones
  kept, and the body used instead of the range. A fourteenth, not clearing the zones at a hole, changes no
  output: a zone from before the hole expires before the touch reports again. The line stays because a run after
  a hole is a frame of its own.
- `judgement_fingerprint()` is `f50dfce1c444`, as before.

**What it costs, and it is more than the first nine columns did.** For one frame of factory depth at 1h (24,000
synthetic bars, candles only) `build_feature_rows` went from 3.33 s to 3.49 s on this host and its peak memory
from 267 MB to 349 MB: about 3.4 KB more per row. The first PR's note gave 16 MB for eighteen keys; that was
another harness (mock bars; it read 282 MB where this one reads 267 MB), so the two are not one series, but the
size of the step is real and has a cause. A row was 170 keys, exactly what a Python dict's table holds before it
doubles, and the 171st key takes each row dict from 3,328 to 6,576 bytes (`sys.getsizeof` on the host's Python
3.14; the container runs 3.12 and was not measured). Any one new column would have paid this. The next doubling
is at the 342nd key.

What that means for a fire depends on how many frames are alive at once, and that is not one:

- The factory builds one frame per leg and keeps them for the whole fire. At 4h and 1d a fire mints across its
  cohort (`factory.POOLED_TIMEFRAMES`), so it holds one frame per symbol: about 20 MB more per 4h leg (6,000
  bars) and about 7 MB more per 1d leg (2,000 bars).
- At 1h the factory's own mints hold one frame (about 82 MB more), but a fire that screens a proposed trial
  builds the frames of every cohort leg at 1h too.
- How many symbols a cohort has is per-machine schedule state and was not read. A comment in `scheduler.py` says
  five symbols are mined. If it is five, a 4h fire holds about 100 MB more and a 1h fire that screens a trial
  about 410 MB more (on roughly 1.3 GB by the same arithmetic). Neither figure was measured.
- Whether any schedule on this machine mines 15m was not read either. A 15m frame is 96,000 bars: about 330 MB
  more per frame by the same arithmetic.
- The higher-timeframe and reference legs build rows of their own while a frame is built.

The factory runs in the maintenance lane (`scheduler-maint`). Neither scheduler container has a memory limit in
`docker-compose.yml` (only `operator` and `pipeline-worker` do); the host has 3.8 GB of memory and 8 GB of swap.

**Not known.** Everything about market bars: how often a zone is born, how often it is touched, how much those
bars coincide with the bars `fvg_return_*` and `structure_fade_*` already enter on (on synthetic bars the share
moved between about three and five in ten with the arbitrary wick size, so it says nothing either way), and
whether a 1d holdout would hold 25 touches. The hypothesis form for an order-block family has lines 1 – 3 empty
(§191.7: no measured counterparty, no reason it would persist, and the only crypto figure found is negative, from
one vendor). That is recorded, and it is why a family is a separate question.
