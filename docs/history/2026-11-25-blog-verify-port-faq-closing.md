# 2026-11-25 — Blog department: the verify port asks for the FAQ and reads the real closing

**What.** A review of the Naver drafting prompts and the daily run (vault
`analytics/네이버-프롬프트-시스템-검토-2026-11-25.md`) found two gaps in the vault checkers. Thomas approved the fixes
with their defaults ("기본값대로", 2026-11-25). The vault files changed in vault f7c3a5cd, and this PR regenerates the two
ports with `scripts/blog_vendor.py`.

- **Preflight `Q2`.** The FAQ rule of 2026-11-07 (2–4 questions in the last numbered section) had no check. Two daily runs
  read only part of the drafting prompt, and all 7 drafts they wrote lack the FAQ. On the other runs it was 1 draft in 37.
  `Q2` now FAILs a general Naver draft whose last numbered section has fewer than 2 or more than 4 `**Q. ` questions.
  The myth-check form has no FAQ by design and affiliate drafts are out of scope.
- **Variety `V4`.** The closing label was read from the section before the last `---`. That matched the old layout, where
  a separator stood before `**참고 자료**`. In today's layout the closing, the related links and the references all follow
  the last separator, so the checker read the last numbered section instead of the closing. Of the 33 general drafts
  written since 11-14, 16 carried a V4 WARN. `closing_mold` now reads the text after the last separator, up to the related
  links, and knows the one-line answer tally of the myth-check form.

**Both start with drafts created on 2026-11-29.** The E1/E2 title and first-screen trial assigns drafts until 11-28, so a
change that can alter a draft waits for the next day. A draft created earlier is judged exactly as before. The old V4 path
can be removed once every draft created before 11-29 is published.

**Checked.**
- With the Q2 date moved back to 11-08, the check flags exactly the 9 drafts counted by hand (209, 227, 231–234, 244–246).
  At the real date it flags no existing draft.
- Read with their own creation date, the closing label of all 262 Naver posts is unchanged. Read the new way, the same 33
  drafts would carry 11 V4 WARNs. Six are real overlaps (a draft whose closing lacks its form's blanks or table, or two
  drafts that both close with a table). Five are a 절차형 next to a 사례해부형: both forms prescribe a one-line prose closing
  and the checker cannot tell them apart, so V4 still fires there without cause.
- The vault regression tests pass with 17 new cases (scripts 03cc85c). The 3 Q2 FAIL cases fail against the old preflight,
  and the new V4 cases cannot run against the old variety, whose `closing_mold` takes no creation date.
- `tests/test_mvp_runtime_blog_verify.py` pins both behaviours on the ports.

**Not in this PR.** `blog_vendor.py --check` also reports `tistory_queue.py`, `kw_pipeline.py` and `affiliate_link.py` as
stale. They were stale before this change (vault edits of 11-21 and later). Regenerating `tistory_queue.py` moves
`NEW_PER_DAY` from 3 to 1 and fails `test_a_slot_the_queue_would_give_passes_every_check`, so those three need their own PR.
