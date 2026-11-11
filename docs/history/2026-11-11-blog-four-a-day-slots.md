# 2026-11-11 — Blog: four posts a day (three regular, one fast) and the new windows, in the slot shadow

**Why.** On 2026-11-11 Thomas decided four posts a day on both Naver and Tistory. Three are regular: they are written
as now and published 13–15 days later. One is fast: it is written at night and published the next evening, and the
slot stays empty on days with no timely topic. Posts go up at random times within 10–11, 13–14, 16–17 and 19–20 KST,
and 19–20 is the fast slot. Fast posts stay out of the E1/E2 experiment. Vault `45b4cb52` changed the two slot rules
that the P7 shadow vendors:

- **`tools/naver_slot.py`**
  - From `CUTOVER` (2026-11-22; Naver reservations run to 11-21), regular drafts take the 10:00, 13:00 and 16:00
    windows. The window comes from the order within the day, because old 15:xx and 18:xx times overlap the new 16:xx
    and 19:xx windows.
  - `fast: true` drafts take 19:10–19:50 on the day after they were written (`next_fast`), at most `FAST_MAX` (1) a day.
  - Fast drafts are left out of the regular days, windows and `DAY_MAX`. `DAY_MAX` stays 3, because `kw_pipeline`
    reads `naver_reserve.DAY_MAX` as posts per day.
- **`tools/tistory_queue.py`**
  - From `SLOT_CUTOVER` (2026-11-18), the regular slots are 10:30, 13:30 and 16:30, each jittered ±14 minutes.
  - `fast: true` posts take `FAST_SLOT` 19:30, at most `FAST_PER_DAY` (1) a day, outside the only/new caps.
  - `SLOTS` is now the union of the old, new and fast slots, so `key` folds every time.

The fast lane itself is off (`ops/fast-lane.json`) until the Mac confirms the reservation side.

**What.**
- `scripts/blog_vendor.py`: the Tistory `KEEP` list gains the names `SLOTS` now depends on: `OLD_SLOTS`, `NEW_SLOTS`,
  `FAST_SLOT`, `SLOT_CUTOVER`, `FAST_PER_DAY`, `is_fast` and `slots_for`. Without them the generated
  `blog/tistory_queue.py` would fail at import. The regenerated ports are `naver_slot.py` and `tistory_queue.py`, and
  `--check` passes.
- `blog/slot.py`:
  - A fast Naver draft is compared with `next_fast` from its `created` day. A fast draft with no `created` gives
    `no_created`.
  - `_tail` reads days the same way the rule does: fast drafts are left out, and days from the cutover on are counted
    by order.
  - For Tistory, fast posts are left out of the only/new counts. For a fast draft, `only_cap` is the fast cap and
    `new_cap` is null. The `blog_slot.v0.3` record keeps its closed shape, so the schema does not change.

**Verified.**
- The slot tests: 31 pass. Three new tests cover the new windows, a fast Naver draft, and the Tistory fast caps.
- Three faults were each caught by those tests:
  - the fast draft counted in `_tail`;
  - fast posts counted in the regular caps;
  - a fast draft compared with `next_slot`.
- `-k "blog or vendor or domain_isolation or gate_env or schema"`: 430 pass. The one failure,
  `test_r35_…` (`AF_UNIX path too long`), fails on origin/main too and comes from the length of the host's socket path.

**Deploy order.** Tonight's drafts (2026-11-11, 23:02 KST) are placed with the new vault rule. A `blog_slot` fire that
compares them on the old port records a disagreement, so deploy before the next `blog_slot` fire after that report.
The keyword pipeline is unchanged here, so the P4a 22:00 deadline does not apply.
