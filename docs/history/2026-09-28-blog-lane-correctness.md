# The blog lane's evidence, draft and budget are fixed in code; the lane stays held

- **Why now:** Thomas asked for §J's verified defects to be fixed in code (2026-09-28). Activation
  was ruled out. The weekly row stays disabled, nothing publishes, and no deployment or runtime
  state changed. Each defect was re-checked against `origin/main` (`6f4c72a0`) first, and all of
  them were real.
- **Selection:**
  - `compIdx` (advertiser bid competition) no longer gates. It is recorded as `ad_competition`.
  - "Already written" is the ledger UNION the vault's post front matter, read by
    `VaultPublishedKeywordSource`. It is matched with `kw_pipeline.covered_by`'s rule: normalized
    equality, 60% containment, or word Jaccard ≥ 2/3; a tag counts only on exact match.
  - With no published source, rule-based selection refuses before it spends any research.
- **Evidence (package v0.2):**
  - `target_evidence` comes from the content run's own brief (`keyword_seeds=target`), which used
    to be discarded. The target's row is found by `normalize_keyword` (NFC, no whitespace,
    casefold), because Search Ad returns `AI회계` for `AI 회계`.
  - The brief now counts blog posts for a seed's own row wherever it ranks, and queries the seed
    as written.
  - A missing or incomplete row gives `status: missing`, never a neighbour's number or a zero.
  - `selection_evidence` keeps the candidates. `lineage` holds the real trace ids of the runs.
- **Draft:**
  - `content.general`'s contract is hash-bound and change-controlled, and a blog field there would
    burden every content request. So the blog request asks for a JSON document inside
    `content_draft`.
  - `blog_draft` parses the JSON, coerces it without inventing anything, and renders it. Only
    level-1 headings count as legacy titles.
  - The lane now reads `content_draft`, not the rendered reply with its review sections.
- **Quality:**
  - One revision runs when the first draft misses a critical standard, the structure, or three
    titles. It names only the failures and does not re-run the brief.
  - `quality_state` is `ready_for_review` or `needs_edit`, and the package is always kept.
  - `body_chars` now counts prose only (`blog_draft_standards.2026-09-28`, numbers unchanged).
  - The package is validated against its schema before it is appended. It was never validated
    before.
- **Fact checks:** model-flagged claims plus a detector for price, free tier, limits, version,
  date, API, policy and availability. A source counts only if it resolves to a non-mock hit the
  run had.
- **Budget (B9):** the `blog_content` profile (16,000 per agent) is sized into the task
  allocation and the specialist's assignment together. It is refused on any request kind but
  `content`.
- **Phase 4:** `blog_rank_snapshot.v0.1` holds append-only D+1/7/14/28 observations through the
  existing blog search client. `scripts/track_blog_rank.py` is hand-run and refuses the Mock. No
  schedule is registered, and nothing feeds selection.
- **Left open:**
  - The seeds are still fixed.
  - The worker does not mount the vault.
  - There is no SERP reach gate in the runtime.

  See §J.
