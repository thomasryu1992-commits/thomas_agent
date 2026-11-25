# 2026-11-25 — price-structure columns on the feature row, as evidence only

`runtime/mvp_runtime/crypto/features.py` (`_structure_columns`, `STRUCTURE_COLUMNS`),
`tests/test_mvp_runtime_crypto_structure_columns.py`, `tests/test_mvp_runtime_crypto_point_in_time.py`.

**What it is for.** Thomas asked for the strategy design to be checked layer by layer (data, indicators,
combination) and named indicators the factory has no column for: breaks of structure (BOS, CHoCH) and fair value
gaps (FVG). The record is `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §183 – §187 (on the research branch,
PR #1474). The definitions are fixed in §185, the outside evidence was surveyed in §186 (none found after costs
in the searched scope), and Thomas decided to put them in anyway (2026-11-25, "그래도 넣어 봄"). §187.5 splits
that into two PRs. This is the first: the columns are computed and carried on the row, and **nothing the factory
makes can read them**. Admitting them to the vocabulary and adding template families is the second PR, with its
own questions.

Those sections are on a research branch that is not merged, so this file carries what the code rests on.

**The definitions** (closed bars only; H, L, C are a bar's high, low, close):

| | Definition | Known when |
|---|---|---|
| Swing high | bar s's high is strictly above the highs of the two bars before and the two after; a tie is no swing | bar s + 2 closes |
| Swing low | the mirror, on lows | the same |
| Live level | the high (low) of the most recent confirmed swing high (low); a new swing replaces it; a close through it removes it until the next swing confirms | as the swing |
| Break | a close above the live high level, or below the live low level. Wicks do not count | that bar's close |
| State | the direction of the last break | the same |
| BOS / CHoCH | a break with the state is BOS, against it CHoCH (and the state flips). They are one event; only the state before differs | the same |
| FVG | this bar's low above the high two bars back (up), or its high below that low (down). The band runs between them | that bar's close |
| Touch | a later bar reaches the band's near edge (the top of an up band, the bottom of a down band); the band is then gone. If a bar reaches several, the most recently formed one is reported | that bar's close |

A live low level always sits below a live high level, so one bar cannot break both.

**What a row now carries** (nine columns, each also as `prev_*`):

| Column | Meaning |
|---|---|
| `swing_high_distance_atr`, `swing_low_distance_atr` | (close − the live swing level) ÷ ATR; None when no level is live |
| `structure_state` | `UP` / `DOWN`: the direction of the last break |
| `structure_break` | `NONE`, `BOS_UP`, `BOS_DOWN`, `CHOCH_UP`, `CHOCH_DOWN` |
| `structure_break_atr` | how far the close went through the level, ÷ ATR, signed; 0 with no break |
| `fvg_size_atr` | width of a gap formed on this bar ÷ ATR, signed; 0 with none |
| `fvg_touch`, `fvg_touch_size_atr`, `fvg_touch_age_bars` | the band this bar reached (`UP_BAND` / `DOWN_BAND` / `NONE`), its size, its age |

**Choices that are not obvious.**

- *Evidence only is the whole point of this PR.* The columns are in neither `template_space.NUMERIC_FEATURES`
  nor `CATEGORICAL_FEATURES`, so no template, generated spec, fusion child or proposed trial can name one: the
  validator they all pass through answers `BLOCK_UNKNOWN_FEATURE`, and a test asks it. The proposal prompt does
  not list them (tested; the prompt is byte-identical to `origin/main`'s). The judgement fingerprint computed on
  this branch equals `origin/main`'s. The economic-family table and the live path are untouched. The precedent
  is raw `open_interest` and `xs_dispersion`.
- *What "evidence only" does not cover.* A spec an operator imports (`scripts/import_crypto_history.py`,
  `scripts/promote_strategy_candidates.py`) is not validated, and the evaluator reads whatever column a spec
  names. That is true of every evidence column already on the row; it is not new here, and it is not closed here.
- *Who reads a whole row.* Nothing found: the evaluator, the distribution gate, the entry plan and the size
  multiplier each read named keys. No crypto record serialises the row (searched `runtime/mvp_runtime/crypto/`).
- *Three constants, written before any result and not to be moved:* a swing is the middle of five bars
  (`STRUCTURE_SWING_BARS = 2`), an untouched band lives 48 bars (`FVG_BAND_LIFE_BARS`), and a band is touched at
  its near edge. Changing one later changes what every spec that reads these columns was mined on, the
  `ROC_FAST` hazard.
- *A break is a close, a tie is no swing, the level is the most recent confirmed swing.* BOS and CHoCH are the
  same event (a close through a swing level); only the state before it differs. No trend definition is mixed
  in, so CHoCH has no parameter of its own.
- *Start-cut: equal or empty.* The live cycle reads 120 bars (`cycle.py` default) and the replay reads the
  factory depth. A state or a live level leans on the past, so the same bar could read differently in the two.
  Each value is therefore None until the frame itself holds what it needs: a confirmed swing on that side; both
  sides and one break for the state and the labels (that first break sets the state and carries no label); the
  band life for a touch. From then on it equals the full-history value. On synthetic bars a frame of 15 bars
  disagreed on 45 – 99% of bars without this rule (§185.4).
- *The ATR is used one bar later than it exists.* A frame's first ATR averages a first true range that has no
  previous close. With bars that open at the previous close the two agree, which is why the first version of
  the start-cut test let a mutant through; the test bars now open away from the previous close.
- *A hole restarts the run.* A bar without a numeric high, low or close empties its own row, and the bars after
  it are a frame of their own. Nothing is stitched across the hole and nothing is filled with a constant.
- *The point-in-time contract had to be made to see them.* `test_mvp_runtime_crypto_point_in_time.py` refuses a
  fixture that leaves a column empty, because a leak there would compare None with None. It failed on the first
  full run, and rightly: the mock collector sets every high to 1.005 x the larger of open and close, and a bar
  opens at the previous close, so the two bars around every peak share one high. A tie is no swing, so the mock
  can never produce one. The test's frame now moves each high up and each low down by a hair (`_untied`). The two
  swing distances are empty on any bar with no live level (about a fifth of the fixture's bars), so for those two
  the fixture must fill one of the last thirty rows rather than one fixed row. A new test hands every structure
  value to the bar before it and requires the contract to report it. Two consequences worth knowing: a mock run
  carries no swing columns at all, and on real bars a peak that falls exactly on a bar boundary with equal highs
  is no swing (the tie rule of §185.2).
- *`fvg_touch_age_bars` was not in §187.5's list.* With the near-edge rule most bands are reached within a bar
  or two of forming. The age rides along so that a later template can tell an immediate touch from a return
  without anyone reopening the constant.

**What it does not do.** No template reads these columns. No number was fitted and no market data was read: the
tests run on synthetic bars and hand-built bars. Order blocks and Fibonacci retracements have no definition
sheet yet (§187.5: after this PR merges). The hypothesis form for this family has line 1 half filled and lines
2 – 3 empty (§186.3); that is recorded, and it is the reason the second PR is a separate decision.

**What it costs.** For one leg of a factory-depth 1h frame (24,000 mock bars, candles only) `build_feature_rows`
went from 3.50 s to 3.73 s on this host and its peak memory from 282 MB to 298 MB. The pass itself is 0.05 s; the
rest is eighteen more keys on every row (152 to 170: the nine and their `prev_*`). The higher-timeframe and
reference legs build rows of their own, so a full frame pays this more than once.

**Not known.** How often real bars tie at a peak. A peak that falls exactly on a bar boundary gives two bars the
same high, and by the tie rule that is no swing; the mock collector does this at every peak. Nothing was counted
on market data.

**Checked.** 21 tests of their own, and the point-in-time contract on 15m, 1h and 4h frames. The full suite and the
release gate ran green locally (12,366 passed). Four deliberate breaks of the rules were tried against them and each fails at least one
test: the ATR used a bar early, a state set with only one side confirmed, a swing used one bar before it is
confirmed, a touch reported before the frame covers the band life.
