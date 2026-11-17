# 2026-11-17 — Affiliate drafts become model analyses; the ports follow the vault

**What changed in the vault and the server scripts (production).** Thomas 2026-11-17 decided that affiliate (Shopping
Connect) drafts created from 11-17 are written as a *model analysis*. One central model is chosen from the issued links
(ranking and review count), and one or two comparison models give a "value vs. performance" axis. The draft adds an
efficiency calculation per unit of use. The AI link shrinks from a mandatory "AI 활용 절차" H2 with a prompt quote to a
single scene paragraph. Because nobody uses these products, first-hand wording ("써 보니", "사용 후기", "내돈내산") is
forbidden.

The checkers follow, gated on the draft's `created` date so the drafts already reserved keep the old rules:

- `blog_variety.py` `MODEL_FROM = "2026-11-17"`. V12 passes on an AI word anywhere in the body, V13 (three scenes and a
  run result) is not applied, and the new V17 fails first-hand wording (`FIRSTHAND_RX`).
- `blog-affiliate-place.py` (READY gate): the same rules. A body AI word replaces the title/H2 check,
  `firsthand_claim(...)` holds a draft, and the V13 reasons are dropped for new drafts.

**Why.** On 11-17 the scoring was blind to the source: 11 of our affiliate posts against 32 top posts on the same keywords.
Ours scored higher on information and on sales structure, yet ranked lower than general posts of the same age. The blog-tab
rank from the keyword history, 3–14 days after publish, had a median of 7.9 for affiliate posts against 3.7 for general
posts. At 15–28 days, all five measured affiliate posts were outside the top 10. The one consistent gap was first-hand
evidence, which top posts have and ours cannot honestly claim. A model-centred structure is the variable being tested
instead. Report: vault `analytics/제휴-글-채점-2026-11-17/보고서.md`.

**This PR.** It regenerates `preflight.py`, `variety.py` and `affiliate_place.py` with `scripts/blog_vendor.py`.
`preflight.py` also picks up the vault's 11-16 R1 change, which drops `refresh:` in favour of a "YYYY년 M월 기준" sentence
and excludes affiliate drafts. `MODEL_FROM` and `FIRSTHAND_RX` join `SCRIPT_KEEP["affiliate_place"]`, because that port
keeps only named constants. A test pins the date-gated V12/V13/V17 behaviour on the generated variety port. The shadow
only compares, so until this merges, a new-format draft would show a V12 mismatch in the P2 comparison; it changes nothing
in production.
