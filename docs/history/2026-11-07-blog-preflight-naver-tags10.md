# 2026-11-07 — Blog department: the verify port learns the ten-tag Naver rule

**What.** Thomas told the session to go through the applicability review of the Naver home-feed benchmark in order
(vault `analytics/benchmark/홈판-적용검토-2026-11-07.md`, "순서대로 진행해줘"). Candidate 5 raises the Naver tag count
from 8 to about 10. Popular home-feed posts have a median of 10 hashtags, and 64 of the 107 collected posts have 10 or
more. All of our drafts have exactly 8. The evidence is weak (tag count is not shown to move rank or clicks), and the
change is cheap. It applies to both arms of the E1·E2 experiment alike, so it does not confound that test.

The vault side changed `tools/blog-preflight.py` (vault e4239bd1). Check T3 now depends on `created`. A Naver draft
created on or after 2026-11-08 gets a WARN unless it has 9–11 tags. An older draft keeps the old rule: a WARN unless
it has exactly 8 tags. The drafting skill (`naver-blog-draft`, skills 707baf2) now asks for about 10 tags.

This PR regenerates the P2 port, `runtime/mvp_runtime/blog/preflight.py`, with `scripts/blog_vendor.py`. That way
`blog_verify` keeps giving the same verdict as the cron on drafts written from 2026-11-08.

**Why date-gated.** All 207 existing drafts have 8 tags, and most of them are already saved or scheduled on Naver,
which publishes its own saved copy. A plain 9–11 rule would put a new WARN on every one of them. `STYLE_FROM` (Y1–Y3,
2026-10-16) set the precedent for this gate.

**Checked.**
- `blog_vendor.py --check` reports that the port matches the vault.
- Blog lane tests: 167 passed.
- The vault preflight regression test passes with 4 new T3 cases (scripts 3a910c5).
- Old and new preflight were run side by side on all 207 Naver drafts: 0 T3 verdicts changed.
