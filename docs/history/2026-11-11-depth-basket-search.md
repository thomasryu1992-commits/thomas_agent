# 2026-11-11 — the depth basket search: the first new data since the metrics files

`docs/proposals/DEPTH_BASKET_SEARCH_V0.1.md`, `scripts/research/depth_basket_search.py`.

**Why this one.** After the flow and trend searches closed, Thomas picked the one direction that reads data no
search has read: the archive's `bookDepth` files. Everything else on 2020–2024 has been ranked many times.

**Two probes before any rule was written.** Which sign is the bid side was never needed by the depth-cost tool (it
took the thinner side); a 2023 file settled it from the average price of each band against the mid. And the first
available day of all 123 universe symbols was listed, so the development span rests on dates, not on a hope that
the files exist: every member of every month from 2023-02 has files from 36 days before it.

**One hypothesis per attempt.** The first sketch had three variants of one idea (band, lookback). Twice in this
repo a pass died when only the lookback changed, so a variant is not an attempt: each attempt is a hypothesis with
its own reason, its neighbours are run beside it and shown only, and a pass whose neighbour falls under 0.5 is
written down as fragile with no forward slot asked for.

**The stop rule for the counts was written first.** M03 ranked 22 of 30 because a missing file looked like an
unrankable coin. Here an unranked member-decision must map to one of three named causes, or nothing runs.

**The driver lives in the tool.** The refill for M04 ran from a scratch file. `fetch` here is resumable, retries a
network error instead of recording it as missing, and stops inside the quiet window.

**What the fetch taught.** The log showed one line for hours because Python block-buffers a redirected stdout, so a
mid-run read said "no retries" when six had happened (503s on two spans, all recovered). The tally also
undercounts: files kept by an attempt that later raised come back as "present" on the retry. The file times, not
the tally, show nothing predated the run. And the probe's "every member has files from 36 days before" was wrong
for one symbol (BNXUSDT starts a month late); why the probe missed it was not traced. The counts caught it as a
named cause, which is what the stop rule is for.

**The sign was checked on raw files before the run,** by code that shares nothing with `daily_values`: the −1% band's
average price sits below the +1% band's on four files, and the hand-computed imbalance equals the derived line to
the last digit. D01 then came out at −1.652 with both neighbours beside it. Because the sign was pinned first, that
is a finding about the market and not a bug to chase; because the direction was pinned first, it is not a pass
either. The reverse was not computed on this span.

**Result.** D01 −1.652, D02 0.838 (floor cleared at 17.56%/yr on total position, Sharpe not). Closed after two
attempts; nothing goes to a forward screen.

