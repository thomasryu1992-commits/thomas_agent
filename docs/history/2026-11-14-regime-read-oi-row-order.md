# 2026-11-14 — Regime strategy set: a day's open interest is the row with the latest stamp

**What.** `regime_strategies.read_oi` now reads `sum_open_interest` from the day file's row with the latest stamp.
It used to read the file's last row. Of rows sharing the latest stamp the later one in the file is read, so a file
in time order gives the same row as before.

**Why.** Some archive metrics day files are not in time order. On those the last row is a mid-day reading, not the
23:55 one the record names. The same files tripped the open-interest reader of discovery-loop round 15 (PR #1470),
which is how this was found.

**How much (inputs only, to 2026-09-30).** Of the 55,183 day files the run could ask for (`oi_plan`), 4,524 are out
of order and 4,084 give a different value: 2024-04/05/07/08/09 and 2026-04..09, no other year. The gap is small on
most days (median 0.63% in 2024, 0.17% in 2026) and large on a few (up to 44%). No file has two different values
at its latest stamp, and neither reader skips a file the other reads.

**What it reaches.** Only S11 and D11 read open interest. The four stage-2 candidates (B08, B02, B07, S05) and
v0.2's four hypotheses do not, so their verdicts do not depend on this reader. v0.2's `stage0` did re-measure all
36 strategies through it: §2.2's S11 figure (excess t 1.05) and stage0's D11 row were read with the last-row reader
and were not re-measured.

Counting the open-interest condition alone on member days to 2026-09-30 (no price, label or return joined), D11's
condition flips on 22 member-days of the screen spans and S11's on 7. For D11 that bounds the entries that can
change. For S11 it does not: its signal strength is the open-interest change itself, which orders the day's signals
under the six-position cap, so a differing file can change which legs are taken where both readers say true.

**What is not known.** How far S11's and D11's stage-1 rows move. S11 sat at t 1.33 on 36 trades against the
screen's 1.5, so a few changed entries could carry it either way; D11 sat at t 0.06. Finding out means running
stage 1 again, which after stage 2 is a new test, on holdout quarters that are spent. It was not run.

**What was stepped past.** v0.2 kept this module byte-for-byte so v0.1's records keep reproducing under their
`code_digest`. This fix moves it from `688b8928b888bd84` to `65f6e2a25b6e2da1`; the records reproduce at their own
commit (`394c3f2a`), not at HEAD. v0.2's digest had already moved on 2026-11-10 (#1424 changed
`cross_section_basket.py`).

**What did not change.** No status line, no verdict, nothing reopened. No 23h floor and no day filter were added:
84 of the `oi_plan` files have a next-day stamp as their latest row, and both readers read that same row.

**Test.** `test_open_interest_is_the_days_latest_reading_whatever_order_the_file_is_in`: an in-order file, an
out-of-order file and a file with two rows at the latest stamp. With the last-row reader restored the out-of-order
case fails. Over the cache (whole folders, so 756 of the days fall after 2026-09-30), the fixed reader and main's differ on
5,711 of 68,155 day files, every one of them an out-of-order file.
