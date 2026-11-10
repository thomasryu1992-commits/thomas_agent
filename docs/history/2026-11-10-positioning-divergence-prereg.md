# 2026-11-10 — Positioning divergence v0.1: the library's two unminted families, measured on the archive, pre-registered

**What.** `docs/proposals/POSITIONING_DIVERGENCE_V0.1.md` pre-registers one measurement: the two positioning
families `template_space` has held unminted, at their base parameters, on six symbols' 1h bars over 2023-01 to
2026-09, judged by the screen gate's entry floor (annual Sharpe 1.0). It is option 3 of
`CRYPTO_DESIGN_REVIEW_V0.1.md`.

Added:
- `scripts/research/positioning_divergence.py`.
- `tests/test_research_positioning_divergence.py`.
- A READ row for the script in `tests/test_mvp_runtime_crypto_script_roster.py`.

**Why these two families and nothing new.** Choosing a signal now would be a free parameter. The families, their z
threshold and their exits were written before this measurement, so the only choices left were the span and the
symbols, and both are fixed by what the archive holds.

**Why the reading labelled T is the row at T − 5 minutes.** The runtime stores what the venue's API calls the
hourly reading at T. On the two weeks where the runtime's store and the archive overlap, that value is the archive's
row created five minutes earlier, for all three series, 335 of 335. The row at T itself is a different number. The
OI lookahead (2026-10-20) came from an aggregation rule nobody had checked against the live source, so this one was
checked first and a mutation test holds it.

**Why 2022 is left out.** The coverage run printed counts only: 18% of hourly readings were missing for every
symbol, with no file missing. The two top-trader columns are empty for most of 2022. The span was cut to 2023 onward
before any trade or return was computed, and the proposal says so.

**Why the stale rule is the live one.** A missing hour would otherwise let a bar trade on an old reading, because
the as-of join carries the last value forward. Live refuses a reading older than three hours (decision 28); the
replay clears the same bars.

**Why a pass does not mint.** `MINTING_DECIDED` is Thomas's switch and stays False. A pass sends the strategy to
the four-month forward screen; whether the factory mints these families is asked separately.
