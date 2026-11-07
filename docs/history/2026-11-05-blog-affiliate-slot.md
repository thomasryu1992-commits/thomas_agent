# 2026-11-05 — Blog department: the affiliate placement's slot is compared too (P7)

**What.** P7's switch condition is two weeks in which the instruction's slots and the real reservations are the same.
`blog_slot` recomputed every reported Naver draft's slot, but an affiliate post never reached it:
- `blog-affiliate-place.py` turns a stock draft into `content/naver/제휴/<N>편-<slug>.md` with the slot the writer
  chose.
- The run report lists that file under `affiliate_placed` (#1379), not under `drafts`, and carried only its path and
  number.

The proposal's §3-2 recorded the gap ("제휴 글의 예약 슬롯 비교는 아직 없다"). After this change, every Naver post the
cron schedules is compared.

**Change.**
- **Server scripts (git e104c4e).** `blog-run-report.py` adds each placement's `scheduled`, read from the file when the
  report is written. The drafting shell runs `naver_slot.py fix` before the report, so the value already has the
  rule's minutes, as the drafts' values do.
- **`blog/slot.py` (record `blog_slot` v0.3; v0.1 and v0.2 stay for their records).**
  - Each placement goes through `compare_draft`, unchanged. The vault's rule already reads the 제휴 folder, so a
    placement's moment is rebuilt the same way: the drafts now, minus every number above it.
  - Rows carry `affiliate`. The summary adds `affiliate`, `affiliate_compared` and `affiliate_agreed`. Placements also
    count in the Naver totals, so the weekly line ("네이버 비교 N · 일치 N") and `blog_department_week` need no change.
  - A placement that the report also names as a draft is counted once.
  - A report written before the slot was carried gives `no_slot`.

**Checked on the real vault (2026-11-05).**
- 198편 (placed 11-04): the rule gives 2026-11-17 15:20, the slot it holds.
- `fire` over the 11-02…11-05 reports, with an empty ledger, records all four as v0.3: Naver 11/11 agree, Tistory 12/12
  pass. No report before 11-06 has a placement with a slot.
- The earlier placements (86–156편) are published. A rebuilt moment is meaningless for them, as it is for any published
  draft.
