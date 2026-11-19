# 2026-11-19 — Affiliate link check and keyword rank budget; the ports follow the vault

**What changed in the vault and the server scripts (production).** Two things went wrong on 11-18 and 11-19. Both were
fixed outside the system on 11-19, and this PR regenerates the ports so the shadow compares like with like.

- **Affiliate link check** (`affiliate-clips.py`, `blog-affiliate-place.py`, scripts b30ab96):
  - Thomas clipped the Apple Watch 12 product page twice. One clip carried the SE 3 Shopping Connect link
    (`naver.me/FsoYcdg1`).
  - The script now compares every clip's link, and every keyword-note line's link, with the issued link of the same
    product number (`ops/affiliate-link/issued.json`, written by the Mac's `affiliate_link.py`).
  - A clip that disagrees is not copied into the keyword note and is no price evidence for any product. It is printed
    as `⛔ 링크 불일치`.
  - A stock draft linking a product whose note line disagrees is held by the READY gate as `link_mismatch(...)`.
  - Without an issuance record (links taken by hand before the issuing tool), nothing can be compared and nothing is
    said.
  - Replaying the deleted 11-18 clip against the real `issued.json` printed the ⛔ line and left the note untouched.
- **Keyword rank budget** (vault `tools/kw_pipeline.py` 426f7d04):
  - The 11-19 root run of `kw-daily.sh` hit its `timeout 45m` (exit 124) while tracking the blog-tab rank of 203
    published posts. That stage runs just before `queue.md` is written, so no queue was written, and the drafts used the
    11-16 queue.
  - Full runs had grown from 22 to 44 minutes. Rank tracking re-fetches every post's search page (about 400 fetches),
    and the post count only grows.
  - Rank tracking now stops 38 minutes after the run starts, and the queue is written anyway.
  - Posts are measured in this order: those published within 14 days first (E1/E2 read the rank 3–14 days after
    publish), then the post measured longest ago. A post that is cut is measured first next run.
  - A post not measured leaves no history row, so no stale rank is written as today's. "지난번" now comes from the latest
    history file that holds that post, not just the latest file.
  - The SERP and rank stages print minutes since the run started, so the next overrun can be located.

**This PR.**
- Regenerates `kw_pipeline.py`, `affiliate_clips.py` and `affiliate_place.py` with `scripts/blog_vendor.py`.
- `ISSUED`, `_IDX`, `issued_index` and `link_conflict` join `SCRIPT_KEEP["affiliate_clips"]`. The generated
  `configure()` points `ISSUED` at the vault and drops the cached index.
- The rank budget reads its clock through `time.time()` in call form, which the vendor rewrites to the recorded clock.
  A replay therefore decides what to cut exactly as the recorded run did.
- A test runs the generated clip port on a small vault. It checks two things: the mismatched clip is flagged and is no
  evidence, and with no issuance record nothing is compared.

**Known gap.** The container mounts `제휴/키워드` and `제휴/클립` but not `ops/affiliate-link`. In the system the port
therefore finds no issuance record and compares nothing, which is the script's own answer for a missing file. The
morning instruction's affiliate pick can name a stock that the cron's gate holds for `link_mismatch`, but only while a
mismatched clip exists. Mounting `ops/affiliate-link` read-only would close this. That is a compose change for a
separate decision.
