# 2026-11-14 — discovery loop round 17: M04's score on the next tier

**Shipped (registration and tools; nothing run in this commit):** `scripts/research/strategy_ladder.py`
(`with_positioning`, `WideTier`, `wide_counts`, `wide_run`; the commands `wide-counts`, `wide`),
`docs/proposals/STRATEGY_DISCOVERY_LOOP_V0.1.md` §31.

**Why now.** Round 16 (H-B1) closed and #1473 was merged on Thomas's word. Asked what comes next, with the on-disk
hypotheses that name a payer used up, the candidate put to him was: widen the positioning files to ranks 31-100 and
read M04's score on coins outside the month's top 30. Thomas 2026-11-14: "그 후보로 진행".

**Choices that are not obvious.**

- *A new hypothesis in the ladder, not a fifth attempt in M04's protocol.* `metrics_basket_search.CANDIDATES` and
  its budget are untouched; the score, its features and the basket rule are imported from it, and a test pins that
  the book equals M04's weigher called on the tier's members. A miss changes nothing in M04's verdict or screen.
- *Judged on the held accounting at 26 bps, with M04's own accounting printed beside it.* M04's 1.134 is the
  engine's daily-reset accounting at 8 bps. The engine value on the new coins is the like-for-like replication; the
  held value is what the ladder judges. M04 itself is not re-measured under the held accounting: that would be
  re-judging it.
- *The null is a slow score.* A score redrawn every week turns a fifth-and-fifth book over almost completely; a
  30-day feature barely moves. The bar run had shown the gap (5.2% a year in costs against 0.9%). So the null is
  per-coin noise averaged over the score's own lookback, on exactly the coins the real score ranks, and the counts
  print the book's turnover beside the nulls'. Outside a registered band the p is labelled as mixing "trades less"
  with "picks better".
- *Every member's window is checked, not only the unranked ones.* The features tolerate missing days, so a hole
  left by an incomplete fetch would thin a ranked coin's window without showing. A test world with two unfetched
  days in a ranked coin's window drove this: the first version of the counts did not stop on it.
- *Two kinds of stop.* A day with no file and no recorded 404 is a tooling stop (finish the fetch or the build,
  count once more). Too few ranked coins or decisions is a stop of the definition (close).
- *The fetch started before the registration.* It reads no return, and the plan depends only on tier membership
  and the 30-day lookback. Its plan ends on 2024-12-31. A detached driver with three workers and one writer for the
  archive's missing list replaced parallel calls of `fetch`, which would have raced on that file.
- *What had been seen is in the form.* The sweep read both variables singly on the top 30 under the held
  accounting and neither stood apart from random books; the map's cells for this tier were empty. The size line
  carries M04's record to 26 bps (16.7% a year of total position, at the floor before two discounts).

**Results (same day).**

- *The data came in whole.* 88,168 files fetched between 09:00 and 11:50 UTC, 555 days the archive does not serve,
  no error; a second pass had nothing to fetch. The rebuild took the derived daily values from 68,155 rows to
  156,323 with every old row unchanged. The fingerprint recorded here uses a stated formula; the one written on
  2026-11-11 was computed some other way and cannot be compared, so the evidence that M04's inputs did not move is
  the row comparison and the files' own timestamps.
- *The counts did not stop the book, and the null was a fair one.* No read day without a file or a cause; 91% to
  99% of the tier ranked; 13 to 14 coins a side; the book's weekly turnover 0.93 of its null books'. Costs alone
  came to 11% of total position a year, so the book needed M04's own gross to reach the floor.
- *H-M1 failed every rung and is closed.* Annual Sharpe -0.698 net over three years, -17.0% of total position a
  year, every year negative, p 0.342 beside 300 books on slow noise. Price was negative before costs.
- *It does not replicate under M04's own accounting either.* The engine accounting at 8 bps, printed beside the
  verdict as registered, gives -0.122 where M04 on the top 30 gave 1.134.
- *What this does not do.* M04's verdict and forward screen stand as registered, and M04 was not re-measured on
  the top 30 under the held accounting. Whether M04 is an effect of the largest coins or a value that came up by
  chance there is not something this result separates; the registered screen is what does.
