# 2026-11-11 — Blog: the fast post goes out the evening it is written, then the next free evening

**Why.** The drafting cron runs `2 23 * * *` on a UTC host, which is 08:02 KST. Earlier the same day the fast lane
was designed as if drafting ran at night, so a fast draft was slotted for the evening of the day after it was written
(`2026-11-11-blog-four-a-day-slots.md`). Once the real time was known, Thomas decided the fast post starts from the
same evening and moves on in order ("당일 저녁부터 순차적으로"). The drafting prompt now writes the fast post first and
pushes it before the Mac's morning routines, which run about 10:40 KST for Naver and 11:35 KST for Tistory.

**What.** Vault `6f95236c` changed `tools/naver_slot.py`:
- `next_fast` returns the first free 19:10–19:50 slot from the day of the run.
- From `FAST_LAST_HOUR` (17 KST) on, it starts from the next day instead.
- A given `today` is taken as the writing day with no hour correction. The shadow passes the draft's `created`.

The port is regenerated, and `blog/slot.py`'s description of the fast comparison is updated to match. The Tistory
queue already started from the same day (`next_free` from today, past slots cut by `MARGIN_MIN`), so it is unchanged.

**Verified.**
- The slot tests: 32 pass, one of them new. The new test covers a taken evening, which moves the draft to the next
  free evening.
- With the old "day after" rule put back into the port, a test fails.
- `-k "blog or vendor or domain_isolation"`: 228 pass.

**Deploy.** The fast lane is still off (`ops/fast-lane.json`), so no fast draft exists yet and nothing is compared
differently until it is turned on. Deploy before the switch goes on.
