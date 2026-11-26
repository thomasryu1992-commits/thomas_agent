# 2026-11-26 — four price-structure family pairs, and three columns admitted to the vocabulary

`runtime/mvp_runtime/crypto/template_space.py` (`STRUCTURE_FAMILIES`, eight templates, three vocabulary
entries), `runtime/mvp_runtime/crypto/features.py` (`STRUCTURE_MINTABLE_*`), `scripts/selection_evidence.py`
(`ECONOMIC_FAMILY_MARKERS`), `tests/test_mvp_runtime_crypto_structure_families.py`.

**What it is for.** The second of the two PRs `2026-11-25-structure-columns.md` describes. The first put nine
price-structure columns on the feature row as evidence only. This one lets the factory read three of them and
adds the families that do. The working record is `docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §186 – §190 on
the research branch (PR #1474, unmerged), so this file carries what the code rests on.

**No measured edge stands behind these families.** The outside survey (§186) found no after-cost evidence for
breaks of structure or fair value gaps in the scope it searched, and the nearest practitioner test on perpetuals
was negative after costs. Thomas decided to put them in front of the factory's own yardstick anyway. The
hypothesis form at the end says so line by line.

## The decisions (all Thomas)

| Date | Decision |
|---|---|
| 2026-11-25 | Put the structure indicators into the factory although the survey found nothing ("그래도 넣어 봄"). This reopens, for this family, the 2026-10-01 line that no template is added |
| 2026-11-25 | Break families go both ways: follow and fade ("둘 다 넣음"). Four pairs: BOS follow, CHoCH follow, break fade, FVG return |
| 2026-11-25 | All four pairs at 1h, 4h and 1d ("세 주기 모두") |
| 2026-11-25 | Three markers are added to the frozen `ECONOMIC_FAMILY_MARKERS` ("셋 더함") |
| 2026-11-26 | Follow and fade stay overlapping: both are minted on the same event ("겹친 채 둘 다") |

## What was built

**Three columns became mintable**, the ones a family reads, and six stay evidence only (the open-interest and
positioning split: admit what is read, keep the rest on the row).

| Mintable | Evidence only |
|---|---|
| `structure_break` (label), `structure_break_atr`, `fvg_touch_size_atr` | `swing_high_distance_atr`, `swing_low_distance_atr`, `structure_state`, `fvg_size_atr`, `fvg_touch`, `fvg_touch_age_bars` |

Admission also reaches the model proposer: its prompt lists the vocabulary, so a proposed trial may now name
these three. It cannot name the six.

**Eight templates**, appended to `TEMPLATES` so every earlier family keeps its place:

| Family | Enters when | Exit space |
|---|---|---|
| `structure_follow_bos_long` / `_short` | the close broke the live swing level WITH the structure's state, by at least `break_atr_min` ATR; in the break's direction | trend |
| `structure_follow_choch_long` / `_short` | the same close AGAINST the state (the first break the other way); in the break's direction | trend |
| `structure_fade_long` / `_short` | any such close of at least `break_atr_min`; against the break's direction | fade |
| `fvg_return_long` / `_short` | the bar came back to the near edge of an untouched gap at least `fvg_atr_min` ATR wide; in the gap's direction | trend |

- **Thresholds.** `break_atr_min` and `fvg_atr_min` are each `ParamSpec(0.05, 1.0)` with base 0.3, written before
  any bar was read and not tuned on one. The floor is strictly above zero because both size columns are a real
  0.0 on a bar with no event, so a threshold at zero would match every quiet bar. The three break pairs share one
  space and base, so at the same draw a difference between follow and fade is not a difference in threshold.
- **Exit spaces** follow the library's existing rule: a fade mints from the fade space, a continuation from the
  trend space, and a pullback entry (`trend_pullback`, `htf_pullback`) from the trend space. So a gap between
  follow and fade is direction and geometry together, not direction alone.
- **Conditions.** The follow families carry the label and the size (two literals). Fade and the gap pair carry the
  signed size alone (one literal): the sign already says which way, and the label would add a counted literal and
  no information. One consequence: fade also takes the first break of a frame, which has a size but no label yet
  (the state it would be judged against is not known; the start-cut rule). That is one bar per unbroken run of
  candles.
- **Timeframes.** `STRUCTURE_FAMILIES` are minted at `STRUCTURE_TIMEFRAMES` = 1h, 4h, 1d and nowhere else. 15m is
  authorable and in the factory's ladder; it was not decided, so it is gated out rather than inherited.
- **Judgement cells.** `structure_follow` → `structure(follow)`, `structure_fade` → `structure(fade)`, `fvg_` →
  `gap(fvg)`, appended to the table. First match wins, so an appended marker cannot move a label an earlier marker
  already placed; a test calls the classifier with and without the three over every family the library has held.
  The pinned hash moved with this decision (`5c73cbd9…` → `16cadabf…`). `SELECTION_EVIDENCE_V0.1` carries the note.

## Follow and fade overlap, and the two precedents

A break up of at least `break_atr_min` matches `structure_follow_*_long` and `structure_fade_short` on the same
row. That is the decision: the evidence on whether a break continues or reverses is mixed, so both readings of
one event are minted and each is judged against its own coin-flip twin.

- **The precedent it follows:** `funding_momentum_*` beside `funding_fade_*` (2026-08-08). They take opposite
  sides of the same funding reading and a test asserts they match one row.
- **The precedent it does not repeat:** `xs_momentum_*` was retired on 2026-08-04 because it fired on the
  *identical* condition as `xs_reversion_*` at every draw. Here the two sides share events, not a whole rule: the
  follow side is split by label and each family draws its own threshold and exits.
- **What it costs, known and accepted.** The factory's judgement is untouched: each strategy has its own forward
  stream and settles whoever was routed. In the paper pool, 4h and 1d contexts do not admit a direction split at
  all (`pool_admission`, an existing rule for every long and short). At 1h both can be admitted, and a bar they
  split on is blocked until one side has positive realized evidence (`paper.route_entries`), both going to the
  shadow book meanwhile.
- Within one pair the long and the short cannot share a row.
- A BOS parent and a CHoCH parent of the same direction fuse into a child that can never enter (two values of one
  label under AND). `mint_fusions` already refuses a child that closed no trades; such a pair spends a draw.

## What changes for what already runs

- **Rotation.** The library at 1h and 4h goes from 42 to 50 families (40 to 48 for the reference symbol), and at
  1d from 26 to 34 (24 to 32), so each earlier family's turn in the plain rotation comes round about a fifth
  later (about a third at 1d). The 1h/4h tilt
  toward six breakout families (`MINT_TILT_FAMILIES`) is unchanged; the half that is not tilted is the half that
  dilutes. No allocation rule was edited. Each context's place in the walk also moves once at deploy: the
  rotation offset is taken modulo the library size (`generator._rotation_offset`), and coverage holds at
  every size (`test_no_library_size_leaves_a_family_unreachable`).
- **The 2027-03-22 judgement** gains up to three cells per timeframe if a cell reaches ten settlement days, which
  tightens the BH line for the other cells of that timeframe a little.
- **Not changed:** the judgement-rule fingerprint (identical on main and this branch, called on both), holdout and
  forward constants, entry routing, the position book, the live path, the structure constants (k 2, band life 48,
  touch at the near edge) and the tie rule.

## Not known

- **Whether the 1d families can be judged.** On a synthetic random walk (not market data) of 3,000 bars the
  follow families match about 40 – 50 bars at the base threshold, fade about 85 – 90 and the gap pair about 220 –
  230. A 1d holdout is roughly 600 bars and needs 25 trades, so on bars like those a follow family alone would
  fall short at 1d. Real bars are not a random walk and the factory may pool symbols; nothing was measured on
  market data.
- How often highs and lows tie on real bars (a tie is no swing). The mock collector's bars tie at every peak, so
  on mock data no structure family ever enters.
- Whether any label already in the candidate store contains one of the three new markers. No template or retired
  family does; the store was not read.

## Hypothesis form (`HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1` §2)

| | |
|---|---|
| 1 Who pays | **Half filled.** The literature names who pays around levels: owners of clustered stops, forcibly liquidated leverage, liquidity demanders at round prices. It names dealers and liquidity providers as who receives. A market order through a level stands on the triggered stops' side. No source identifies a payer for an entry on a return to a gap |
| 2 Why it persists | **Not filled.** What the literature reports is disappearance: US equities after the late 1980s, FX after the early 1990s, BTC 5-minute bars after 2014 |
| 3 Size after cost | **Zero or negative in the searched scope.** The nearest case (perpetuals, prior-day high/low) was −10.4 to −16.4 bp per trade after cost, one practitioner source. No test exists at 1h or 4h |
| 4 What refutes it | The factory's yardstick: a holdout gross per trade under that timeframe's cost, or member minus twin at or below zero; then the forward cohort and the family verdict at the epoch boundary |
| 5 Nearest record | The breaks are relatives of the closed 20-day close breakout (L32 – L34) and of the regime set's B02, D02, D09 (follow) and S08 (fade). They are not the same entry: the level is a confirmed five-bar swing, not a 20-day window, and the universe, exits and judging side differ. The gap pair has no relative in the record |
| 6 Prior | About 3% that any of the eight clears line 4 |

Lines 1 – 3 are not filled from evidence. By the form's own rule that means not putting it on the bench; putting
it on is Thomas's decision of 2026-11-25, recorded above.
