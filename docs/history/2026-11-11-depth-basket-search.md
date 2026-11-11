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
